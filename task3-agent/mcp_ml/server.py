"""MCP-сервер для AppSec- и SecOps-анализа кода, секретов и зависимостей. Работает на хосте.

Запуск из корня репо: uv run python task3-agent/mcp_ml/server.py
Агент в песочнице подключается к host.docker.internal:8765/mcp
"""
import ast
import math
import re
from collections import Counter
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "task3-agent" / "workspace"
mcp = MCPServer("sec-tools")
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


@mcp.tool()
def scan_code(path: str) -> dict:
    """Статический анализ (SAST) файла кода или конфига в workspace/ (Python, JS/TS, C#, Go, Java, SQL, Dockerfile, YAML)."""
    p = safe(path)
    cache_key = ("scan_code", str(p), p.stat().st_mtime)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]
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
    res = {
        "file": path,
        "language": lang,
        "total_findings": len(findings),
        "by_severity": dict(sev_counts),
        "mean_conf": mean_conf,
        "findings": findings,
    }
    _tool_cache[cache_key] = res
    return res


@mcp.tool()
def scan_secrets(path: str) -> dict:
    """Поиск захардкоженных секретов, токенов, приватных ключей и паролей в файле из workspace/."""
    p = safe(path)
    cache_key = ("scan_secrets", str(p), p.stat().st_mtime)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]
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
    res = {
        "file": path,
        "total_secrets": len(secrets),
        "mean_conf": mean_conf,
        "secrets": secrets,
    }
    _tool_cache[cache_key] = res
    return res


@mcp.tool()
def check_dependencies(path: str) -> dict:
    """Аудит файла зависимостей (requirements.txt, package.json, *.csproj, go.mod) на известные CVE и незафиксированные версии."""
    import json

    p = safe(path)
    cache_key = ("check_dependencies", str(p), p.stat().st_mtime)
    if cache_key in _tool_cache:
        return _tool_cache[cache_key]
    text = p.read_text(encoding="utf-8", errors="replace")
    name_lower = p.name.lower()
    packages: list[tuple[str, str, int]] = []  # (name, version, line)

    if name_lower == "package.json":
        ecosystem = "npm"
        data = json.loads(text)
        for section in ("dependencies", "devDependencies"):
            for pkg, ver in data.get(section, {}).items():
                packages.append((pkg, str(ver).lstrip("^~"), 1))
    elif name_lower.endswith(".csproj"):
        ecosystem = "nuget"
        for idx, line in enumerate(text.splitlines(), start=1):
            m = re.search(r'PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', line)
            if m:
                packages.append((m.group(1), m.group(2), idx))
    else:
        ecosystem = "pypi"
        for idx, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#"):
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
        if ver in {"*", "latest", ""} or ver.startswith((">=", "~=", ">")):
            unpinned.append({"line": line_no, "package": pkg, "spec": ver, "cwe": "CWE-1104"})
        clean_ver = ver.lstrip("=<>~^")
        if key in KNOWN_VULN_PACKAGES and clean_ver in KNOWN_VULN_PACKAGES[key]:
            cve, sev, cvss, desc = KNOWN_VULN_PACKAGES[key][clean_ver]
            vulnerabilities.append({
                "line": line_no,
                "package": pkg,
                "version": clean_ver,
                "cve": cve,
                "severity": sev,
                "cvss": cvss,
                "description": desc,
            })

    res = {
        "file": path,
        "ecosystem": ecosystem,
        "total_packages": len(packages),
        "vulnerable_count": len(vulnerabilities),
        "unpinned_count": len(unpinned),
        "vulnerabilities": vulnerabilities,
        "unpinned": unpinned,
    }
    _tool_cache[cache_key] = res
    return res


@mcp.tool()
def read_code_slice(path: str, start_line: int = 1, end_line: int = 60) -> dict:
    """Прочитать фрагмент исходного кода или конфига из workspace/ с номерами строк (до 100 строк за вызов)."""
    p = safe(path)
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
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
