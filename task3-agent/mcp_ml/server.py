"""MCP-сервер для AppSec- и SecOps-анализа кода, секретов и зависимостей. Работает на хосте.

Запуск из корня репо: uv run python task3-agent/mcp_ml/server.py
Агент в песочнице подключается к host.docker.internal:8765/mcp
"""
import ast
import math
import re
from collections import Counter
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("sec-tools")
    _MCP_V2 = True
except ImportError:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        "sec-tools",
        host="127.0.0.1",
        port=8765,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    _MCP_V2 = False

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
_tool_cache: dict[tuple, dict] = {}

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".cs": "csharp",
    ".go": "go",
    ".java": "java",
    ".sql": "sql",
    ".sh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
}

# Мультиязычные SAST-правила: (rule_id, cwe, severity, confidence, languages, title, regex)
SAST_RULES = [
    (
        "SQLI-INTERP",
        "CWE-89",
        "HIGH",
        0.92,
        {"python", "javascript", "typescript", "csharp", "go", "java"},
        "SQL-инъекция: подстановка переменной в строку SQL-запроса",
        re.compile(
            r"(SELECT|INSERT|UPDATE|DELETE)\b.*(\{[^}]+\}|\+\s*[a-zA-Z_]|%s|\$\{[^}]+\})",
            re.I,
        ),
    ),
    (
        "CMDI-SHELL",
        "CWE-78",
        "CRITICAL",
        0.95,
        {"python", "javascript", "typescript", "csharp", "go", "shell"},
        "Инъекция команд ОС: вызов командной оболочки с внешними данными",
        re.compile(
            r"(subprocess\.(run|Popen|call)\s*\(.*shell\s*=\s*True|os\.system\s*\(|child_process\.exec\s*\(|Process\.Start\s*\(|exec\.Command\s*\(\s*[\"'](sh|bash)[\"'])",
        ),
    ),
    (
        "CODE-EVAL",
        "CWE-94",
        "HIGH",
        0.90,
        {"python", "javascript", "typescript"},
        "Динамическое исполнение кода через eval/exec",
        re.compile(r"\b(eval|exec)\s*\("),
    ),
    (
        "DESERIALIZE-UNSAFE",
        "CWE-502",
        "CRITICAL",
        0.94,
        {"python", "csharp", "java"},
        "Небезопасная десериализация (pickle / yaml.load / BinaryFormatter / ObjectInputStream)",
        re.compile(
            r"(pickle\.loads?\s*\(|yaml\.load\s*\([^)]*\)(?!.*SafeLoader)|BinaryFormatter|ObjectInputStream)"
        ),
    ),
    (
        "XSS-DOM",
        "CWE-79",
        "HIGH",
        0.88,
        {"javascript", "typescript"},
        "Межсайтовый скриптинг (DOM XSS) через innerHTML / dangerouslySetInnerHTML",
        re.compile(r"(\.innerHTML\s*=|dangerouslySetInnerHTML)"),
    ),
    (
        "CRYPTO-WEAK-HASH",
        "CWE-327",
        "MEDIUM",
        0.85,
        {"python", "javascript", "typescript", "csharp", "go", "java"},
        "Слабый криптографический хеш (MD5 или SHA1)",
        re.compile(r"(hashlib\.(md5|sha1)\s*\(|createHash\s*\(\s*[\"'](md5|sha1)[\"']|MD5\.Create\s*\()"),
    ),
    (
        "TLS-NO-VERIFY",
        "CWE-295",
        "MEDIUM",
        0.89,
        {"python", "javascript", "typescript", "csharp", "go"},
        "Отключена проверка TLS-сертификата",
        re.compile(
            r"(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|ServerCertificateCustomValidationCallback)"
        ),
    ),
    (
        "PATH-TRAVERSAL-HEURISTIC",
        "CWE-22",
        "MEDIUM",
        0.45,
        {"python", "javascript", "typescript", "csharp", "go"},
        "Потенциальный Path Traversal: открытие файла по составному пути (требует ручной проверки)",
        re.compile(r"(open\s*\(\s*f[\"']|os\.path\.join\s*\(.*request|fs\.readFile\w*\s*\(.*req\.|File\.ReadAllText\s*\(.*\+)"),
    ),
    (
        "CONFIG-DEBUG-ENABLED",
        "CWE-489",
        "LOW",
        0.80,
        {"python", "yaml", "json"},
        "Включён отладочный режим (debug=True)",
        re.compile(r"\b(debug\s*=\s*True|DEBUG\s*:\s*true)\b"),
    ),
    (
        "CONTAINER-PRIVILEGED",
        "CWE-250",
        "HIGH",
        0.91,
        {"dockerfile", "yaml"},
        "Контейнер с избыточными привилегиями (USER root / privileged: true / --no-sandbox)",
        re.compile(r"(^\s*USER\s+root\b|privileged\s*:\s*true|--no-sandbox)", re.I),
    ),
    (
        "SQL-DYNAMIC-EXEC",
        "CWE-89",
        "HIGH",
        0.90,
        {"sql"},
        "Динамическое формирование SQL через EXECUTE / конкатенацию",
        re.compile(
            r"(EXECUTE\s+IMMEDIATE|EXEC\s*\([^)]*(\+|\|\|)|sp_executesql\s+@\w+.*(\+|concat)|\bPREPARE\s+\w+\s+FROM\b|['\"]?(SELECT|INSERT|UPDATE|DELETE)\b.*(\|\|\s*@?[a-zA-Z_]|['\"]\s*\+\s*@?[a-zA-Z_]))",
            re.I,
        ),
    ),
    (
        "SQL-OVERPRIVILEGED-GRANT",
        "CWE-250",
        "MEDIUM",
        0.85,
        {"sql"},
        "Выдача избыточных привилегий (GRANT ALL)",
        re.compile(r"\bGRANT\s+ALL\b", re.I),
    ),
    (
        "SQL-WEAK-HASH",
        "CWE-327",
        "MEDIUM",
        0.85,
        {"sql"},
        "Слабый криптографический хеш в SQL (MD5 или SHA1)",
        re.compile(r"\b(MD5|SHA1)\s*\(", re.I),
    ),
]

SECRET_PATTERNS = [
    ("AWS_ACCESS_KEY", "CWE-798", "CRITICAL", 0.98, re.compile(r"AKIA[0-9A-Z]{16}")),
    ("OPENAI_API_KEY", "CWE-798", "CRITICAL", 0.96, re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("GITHUB_TOKEN", "CWE-798", "CRITICAL", 0.97, re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("PRIVATE_KEY_HEADER", "CWE-321", "CRITICAL", 0.99, re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("DB_URI_PASSWORD", "CWE-798", "HIGH", 0.93, re.compile(r"(?:postgres|postgresql|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@[^\s\"']+")),
    (
        "HARDCODED_SECRET_ASSIGN",
        "CWE-798",
        "HIGH",
        0.86,
        re.compile(r"(?i)(?:api_key|secret_key|jwt_secret|password|passwd|auth_token)\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"),
    ),
]

# Известные уязвимые версии пакетов (Python / npm / NuGet / Go) для локальной проверки SCA
KNOWN_VULN_PACKAGES = {
    "pyyaml": {"5.3.1": ("CVE-2020-14343", "CRITICAL", 9.8, "Выполнение произвольного кода через yaml.load")},
    "requests": {"2.19.0": ("CVE-2018-18074", "HIGH", 7.5, "Утечка заголовка Authorization при редиректе на другой хост")},
    "flask": {"0.12.2": ("CVE-2018-1000656", "HIGH", 7.5, "Отказ в обслуживании (DoS) при разборе JSON")},
    "cryptography": {"3.2": ("CVE-2020-36242", "HIGH", 7.5, "Переполнение буфера в Fernet при больших входах")},
    "urllib3": {"1.24.1": ("CVE-2019-11324", "HIGH", 7.5, "Обход верификации TLS-сертификата")},
    "django": {"2.2.1": ("CVE-2019-14234", "CRITICAL", 9.8, "SQL-инъекция в JSONField/HStoreField")},
    "lodash": {"4.17.15": ("CVE-2020-8203", "HIGH", 7.4, "Prototype Pollution в zipObjectDeep")},
    "jsonwebtoken": {"8.5.1": ("CVE-2022-23529", "HIGH", 7.6, "Удалённое выполнение кода при некорректном secretOrPrivateKey")},
    "axios": {"0.21.1": ("CVE-2021-3749", "HIGH", 7.5, "ReDoS в функции trim")},
    "newtonsoft.json": {"11.0.1": ("CVE-2024-21907", "HIGH", 7.5, "StackOverflowException при глубокой вложенности JSON")},
    "github.com/dgrijalva/jwt-go": {"3.2.0": ("CVE-2020-26160", "HIGH", 7.5, "Обход проверки audience в JWT")},
}


def safe(path: str) -> Path:
    """Пути только внутри workspace/ — иначе выход за песочницу через аргумент тула."""
    p = (WORKSPACE / path).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise ValueError(f"путь вне workspace: {path}")
    if not p.exists():
        raise FileNotFoundError(f"файл не найден в workspace: {path}")
    return p


def _detect_lang(p: Path) -> str:
    if p.name.lower().startswith("dockerfile"):
        return "dockerfile"
    return LANG_BY_EXT.get(p.suffix.lower(), "generic")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return round(-sum((c / n) * math.log2(c / n) for c in counts.values()), 2)


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}...{secret[-3:]}"


def _ast_python_findings(source: str) -> set[tuple[int, str]]:
    """Подтверждение опасных вызовов в Python через AST (номер строки, имя вызова)."""
    hits: set[tuple[int, str]] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                hits.add((node.lineno, node.func.id))
            elif isinstance(node.func, ast.Attribute):
                val = getattr(node.func.value, "id", "")
                hits.add((node.lineno, f"{val}.{node.func.attr}"))
    return hits


_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".unpacked"}
_SECRETS_EXT = set(LANG_BY_EXT) | {".env", ".ini", ".conf", ".cfg", ".toml", ".txt", ".md", ".xml", ".properties"}
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_ARCHIVE_FILES = 200
_MAX_MULTI_ITEMS = 100


def _extract_zip_safe(zip_path: Path) -> Path:
    """Безопасно распаковать .zip в workspace/.unpacked/<name>/ с защитой от Zip Slip и Zip Bomb."""
    import shutil
    import stat
    import zipfile

    st = zip_path.stat()
    if st.st_size > _MAX_ARCHIVE_BYTES:
        raise ValueError(f"размер архива превышает 50 МБ: {zip_path.name}")
    target_dir = WORKSPACE / ".unpacked" / zip_path.stem
    target_root = target_dir.resolve()
    meta_file = target_dir / ".zip_meta"
    stamp = f"{st.st_mtime}:{st.st_size}"
    if meta_file.exists() and meta_file.read_text(encoding="utf-8", errors="ignore") == stamp:
        return target_root

    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > _MAX_ARCHIVE_FILES:
            raise ValueError(f"в архиве {len(infos)} файлов (лимит {_MAX_ARCHIVE_FILES})")
        if sum(i.file_size for i in infos) > _MAX_ARCHIVE_BYTES:
            raise ValueError("распакованный размер архива превышает 50 МБ (Zip Bomb)")
        for i in infos:
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError(f"архив содержит symlink: {i.filename}")
            if i.filename.startswith(("/", "\\")) or ":" in i.filename[:3]:
                raise ValueError(f"опасный путь в архиве: {i.filename}")
            dest = (target_root / i.filename).resolve()
            if not dest.is_relative_to(target_root) or dest == target_root:
                raise ValueError(f"выход за пределы каталога распаковки (Zip Slip): {i.filename}")

        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for i in infos:
            dest = (target_root / i.filename).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, dest.open("wb") as dst:
                while chunk := src.read(65536):
                    written += len(chunk)
                    if written > _MAX_ARCHIVE_BYTES:
                        raise ValueError("превышен лимит 50 МБ при распаковке архива")
                    dst.write(chunk)
        meta_file.write_text(stamp, encoding="utf-8")
    return target_root


def _iter_project_files(root: Path, mode: str) -> list[Path]:
    """Собрать поддерживаемые файлы проекта внутри root для заданного режима (code, secrets, deps)."""
    import os

    out: list[Path] = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for fn in sorted(files):
            fp = Path(cur) / fn
            if fp.is_symlink() or not fp.is_file() or fn == ".zip_meta":
                continue
            low = fn.lower()
            suf = fp.suffix.lower()
            if mode == "code" and (suf in LANG_BY_EXT or low.startswith("dockerfile")):
                out.append(fp)
            elif mode == "secrets" and (suf in _SECRETS_EXT or low.startswith(("dockerfile", ".env"))):
                out.append(fp)
            elif mode == "deps" and (
                low in {"package.json", "go.mod"}
                or low.endswith(".csproj")
                or (low.startswith("requirements") and suf == ".txt")
            ):
                out.append(fp)
    return out


def _scan_code_file(p: Path, label: str) -> dict:
    lang = _detect_lang(p)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    ast_hits = _ast_python_findings("\n".join(lines)) if lang == "python" else set()
    findings = []
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", "//", "--")):
            continue
        for rule_id, cwe, severity, conf, langs, title, pattern in SAST_RULES:
            if lang != "generic" and lang not in langs:
                continue
            if pattern.search(raw_line):
                # если Python AST подтвердил вызов на этой строке — повышаем уверенность
                has_ast = any(lineno == idx for lineno, _ in ast_hits)
                final_conf = round(min(0.99, conf + (0.04 if has_ast else 0.0)), 2)
                findings.append({
                    "line": idx,
                    "rule_id": rule_id,
                    "cwe": cwe,
                    "severity": severity,
                    "confidence": final_conf,
                    "title": title,
                    "snippet": stripped[:160],
                })
    sev_counts = Counter(f["severity"] for f in findings)
    mean_conf = round(sum(f["confidence"] for f in findings) / len(findings), 2) if findings else 1.0
    return {
        "file": label,
        "language": lang,
        "total_findings": len(findings),
        "by_severity": dict(sev_counts),
        "mean_conf": mean_conf,
        "findings": findings,
    }


@mcp.tool()
def scan_code(path: str) -> dict:
    """Статический анализ (SAST) файла, каталога проекта или .zip-архива в workspace/ (Python, JS/TS, C#, Go, Java, SQL, Dockerfile, YAML)."""
    p = safe(path)
    is_multi = p.is_dir() or p.suffix.lower() == ".zip"
    if not is_multi:
        cache_key = ("scan_code", str(p), p.stat().st_mtime)
        if cache_key in _tool_cache:
            return _tool_cache[cache_key]
        res = _scan_code_file(p, path)
        _tool_cache[cache_key] = res
        return res

    scan_root = _extract_zip_safe(p) if p.suffix.lower() == ".zip" else p
    files = _iter_project_files(scan_root, "code")
    stamp = p.stat().st_mtime if p.suffix.lower() == ".zip" else max((f.stat().st_mtime for f in files), default=p.stat().st_mtime)
    cache_key = ("scan_code_dir", str(p), stamp)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]

    all_findings = []
    scanned = []
    for f in files:
        rel = f.relative_to(scan_root).as_posix()
        scanned.append(rel)
        sub = _scan_code_file(f, rel)
        for item in sub["findings"]:
            all_findings.append({"file": rel, **item})
    sev_counts = Counter(x["severity"] for x in all_findings)
    mean_conf = round(sum(x["confidence"] for x in all_findings) / len(all_findings), 2) if all_findings else 1.0
    res = {
        "file": path,
        "scanned_files": scanned,
        "total_files": len(scanned),
        "total_findings": len(all_findings),
        "by_severity": dict(sev_counts),
        "mean_conf": mean_conf,
        "findings": all_findings[:_MAX_MULTI_ITEMS],
        "truncated": len(all_findings) > _MAX_MULTI_ITEMS,
    }
    _tool_cache[cache_key] = res
    return res


def _scan_secrets_file(p: Path, label: str) -> dict:
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    secrets = []
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        for sec_type, cwe, severity, conf, pattern in SECRET_PATTERNS:
            m = pattern.search(raw_line)
            if m:
                token = m.group(1) if m.lastindex else m.group(0)
                ent = _shannon_entropy(token)
                # если строка короткая или шаблонная (например, "changeme123"), уверенность ниже 0.5
                is_weak_placeholder = token.lower() in {"changeme123", "password123", "example_key_000"}
                adj_conf = 0.42 if is_weak_placeholder else conf
                secrets.append({
                    "line": idx,
                    "type": sec_type,
                    "cwe": cwe,
                    "severity": "LOW" if is_weak_placeholder else severity,
                    "confidence": adj_conf,
                    "entropy": ent,
                    "masked_preview": _mask(token),
                })
                break
    mean_conf = round(sum(s["confidence"] for s in secrets) / len(secrets), 2) if secrets else 1.0
    return {
        "file": label,
        "total_secrets": len(secrets),
        "mean_conf": mean_conf,
        "secrets": secrets,
    }


@mcp.tool()
def scan_secrets(path: str) -> dict:
    """Поиск захардкоженных секретов, токенов, приватных ключей и паролей в файле, каталоге проекта или .zip-архиве из workspace/."""
    p = safe(path)
    is_multi = p.is_dir() or p.suffix.lower() == ".zip"
    if not is_multi:
        cache_key = ("scan_secrets", str(p), p.stat().st_mtime)
        if cache_key in _tool_cache:
            return _tool_cache[cache_key]
        res = _scan_secrets_file(p, path)
        _tool_cache[cache_key] = res
        return res

    scan_root = _extract_zip_safe(p) if p.suffix.lower() == ".zip" else p
    files = _iter_project_files(scan_root, "secrets")
    stamp = p.stat().st_mtime if p.suffix.lower() == ".zip" else max((f.stat().st_mtime for f in files), default=p.stat().st_mtime)
    cache_key = ("scan_secrets_dir", str(p), stamp)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]

    all_secrets = []
    scanned = []
    for f in files:
        rel = f.relative_to(scan_root).as_posix()
        scanned.append(rel)
        sub = _scan_secrets_file(f, rel)
        for item in sub["secrets"]:
            all_secrets.append({"file": rel, **item})
    mean_conf = round(sum(x["confidence"] for x in all_secrets) / len(all_secrets), 2) if all_secrets else 1.0
    res = {
        "file": path,
        "scanned_files": scanned,
        "total_files": len(scanned),
        "total_secrets": len(all_secrets),
        "mean_conf": mean_conf,
        "secrets": all_secrets[:_MAX_MULTI_ITEMS],
        "truncated": len(all_secrets) > _MAX_MULTI_ITEMS,
    }
    _tool_cache[cache_key] = res
    return res


def _check_deps_file(p: Path, label: str) -> dict:
    import json

    text = p.read_text(encoding="utf-8", errors="replace")
    name_lower = p.name.lower()
    packages: list[tuple[str, str, int]] = []  # (name, version, line)

    if name_lower == "package.json":
        ecosystem = "npm"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        for section in ("dependencies", "devDependencies"):
            for pkg, ver in data.get(section, {}).items():
                packages.append((pkg, str(ver).strip(), 1))
    elif name_lower.endswith(".csproj"):
        ecosystem = "nuget"
        for idx, line in enumerate(text.splitlines(), start=1):
            m = re.search(r'PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', line)
            if m:
                packages.append((m.group(1), m.group(2), idx))
    elif name_lower == "go.mod":
        ecosystem = "go"
        in_require = False
        for idx, line in enumerate(text.splitlines(), start=1):
            s = line.split("//", 1)[0].strip()
            if not s:
                continue
            if re.match(r"^require\s*\($", s):
                in_require = True
                continue
            if in_require:
                if s == ")":
                    in_require = False
                    continue
                parts = s.split()
                if len(parts) >= 2:
                    packages.append((parts[0], parts[1], idx))
            elif s.startswith("require "):
                parts = s[len("require "):].strip().split()
                if len(parts) >= 2:
                    packages.append((parts[0], parts[1], idx))
    else:
        ecosystem = "pypi"
        for idx, line in enumerate(text.splitlines(), start=1):
            s = line.split("#", 1)[0].strip()
            if not s:
                continue
            if "==" in s:
                pkg, ver = s.split("==", 1)
                packages.append((pkg.strip(), ver.strip(), idx))
            elif re.match(r"^[A-Za-z0-9_.-]+(\s*[>=<~!]+\s*.*)?$", s):
                pkg = re.split(r"[>=<~!]", s, maxsplit=1)[0].strip()
                ver = "*" if pkg == s else s[len(pkg):].strip()
                packages.append((pkg, ver, idx))

    vulnerabilities = []
    unpinned = []
    for pkg, ver, line_no in packages:
        key = pkg.lower()
        if ver in {"*", "latest", ""} or ver.startswith(("^", "~", ">=", "~=", ">", "<")) or ver.endswith((".x", ".*")) or "||" in ver:
            unpinned.append({"line": line_no, "package": pkg, "spec": ver, "cwe": "CWE-1104"})
        clean_ver = re.sub(r"^[=<>~^!\s]+", "", ver)
        vuln_map = KNOWN_VULN_PACKAGES.get(key, {})
        hit_ver = clean_ver if clean_ver in vuln_map else clean_ver.lstrip("v")
        if hit_ver in vuln_map:
            cve, sev, cvss, desc = vuln_map[hit_ver]
            vulnerabilities.append({
                "line": line_no,
                "package": pkg,
                "version": clean_ver,
                "cve": cve,
                "severity": sev,
                "cvss": cvss,
                "description": desc,
            })

    return {
        "file": label,
        "ecosystem": ecosystem,
        "total_packages": len(packages),
        "vulnerable_count": len(vulnerabilities),
        "unpinned_count": len(unpinned),
        "vulnerabilities": vulnerabilities,
        "unpinned": unpinned,
    }


@mcp.tool()
def check_dependencies(path: str) -> dict:
    """Аудит зависимостей (requirements.txt, package.json, *.csproj, go.mod) в файле, каталоге проекта или .zip-архиве."""
    p = safe(path)
    is_multi = p.is_dir() or p.suffix.lower() == ".zip"
    if not is_multi:
        cache_key = ("check_dependencies", str(p), p.stat().st_mtime)
        if cache_key in _tool_cache:
            return _tool_cache[cache_key]
        res = _check_deps_file(p, path)
        _tool_cache[cache_key] = res
        return res

    scan_root = _extract_zip_safe(p) if p.suffix.lower() == ".zip" else p
    files = _iter_project_files(scan_root, "deps")
    stamp = p.stat().st_mtime if p.suffix.lower() == ".zip" else max((f.stat().st_mtime for f in files), default=p.stat().st_mtime)
    cache_key = ("check_deps_dir", str(p), stamp)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]

    all_vulns = []
    all_unpinned = []
    scanned = []
    total_packages = 0
    for f in files:
        rel = f.relative_to(scan_root).as_posix()
        scanned.append(rel)
        sub = _check_deps_file(f, rel)
        total_packages += sub["total_packages"]
        for v in sub["vulnerabilities"]:
            all_vulns.append({"file": rel, **v})
        for u in sub["unpinned"]:
            all_unpinned.append({"file": rel, **u})
    res = {
        "file": path,
        "scanned_files": scanned,
        "total_files": len(scanned),
        "total_packages": total_packages,
        "vulnerable_count": len(all_vulns),
        "unpinned_count": len(all_unpinned),
        "vulnerabilities": all_vulns[:_MAX_MULTI_ITEMS],
        "unpinned": all_unpinned[:_MAX_MULTI_ITEMS],
    }
    _tool_cache[cache_key] = res
    return res


@mcp.tool()
def read_code_slice(path: str, start_line: int = 1, end_line: int = 60) -> dict:
    """Прочитать фрагмент исходного кода или конфига из workspace/ с номерами строк (до 100 строк за вызов)."""
    p = safe(path)
    if p.is_dir() or p.suffix.lower() == ".zip":
        raise ValueError("read_code_slice принимает конкретный файл; для каталога или .zip используй scan_code")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), max(start, min(end_line, start + 99)))
    numbered = [f"{i}: {lines[i - 1]}" for i in range(start, end + 1)]
    return {
        "file": path,
        "language": _detect_lang(p),
        "start_line": start,
        "end_line": end,
        "total_lines": len(lines),
        "content": "\n".join(numbered),
    }


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    if _MCP_V2:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
    else:
        mcp.run(transport="streamable-http")
