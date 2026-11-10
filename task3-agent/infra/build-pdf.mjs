#!/usr/bin/env node
// Конвертация Markdown-отчёта task3-agent/README.md в task3-agent/report.pdf (офлайн через marked + puppeteer).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const taskDir = path.resolve(__dirname, "..");
const npxPkg = path.join(os.homedir(), ".npm/_npx/668c188756b835f3/package.json");
const require = createRequire(npxPkg);
const { marked } = require("marked");
const puppeteer = require("puppeteer");

const mdPath = path.join(taskDir, "README.md");
const htmlPath = path.join(os.tmpdir(), "task3-report-render.html");
const pdfPath = path.join(taskDir, "report.pdf");

const md = fs.readFileSync(mdPath, "utf8");
const bodyHtml = marked.parse(md);

const html = `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <base href="file://${taskDir}/">
  <title>Задание 3. Мультиагентная система — Аудитор безопасности</title>
  <style>
    @page { size: A4; margin: 16mm 14mm; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 10.5pt;
      line-height: 1.48;
      color: #1f2328;
      margin: 0;
    }
    h1, h2, h3, h4 {
      color: #111827;
      margin-top: 1.2em;
      margin-bottom: 0.45em;
      page-break-after: avoid;
    }
    h1 { font-size: 18pt; border-bottom: 2px solid #d0d7de; padding-bottom: 6px; margin-top: 0; }
    h2 { font-size: 14pt; border-bottom: 1px solid #d8dee4; padding-bottom: 4px; }
    h3 { font-size: 11.5pt; }
    p, li { orphans: 3; widows: 3; }
    code {
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 9pt;
      background: #f6f8fa;
      padding: 1px 4px;
      border-radius: 4px;
    }
    pre {
      background: #f6f8fa;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      padding: 8px 10px;
      overflow-x: auto;
      page-break-inside: avoid;
    }
    pre code { background: transparent; padding: 0; font-size: 8.5pt; }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0 12px 0;
      font-size: 8.8pt;
      page-break-inside: auto;
    }
    tr { page-break-inside: avoid; page-break-after: auto; }
    th, td {
      border: 1px solid #d0d7de;
      padding: 5px 7px;
      text-align: left;
      vertical-align: top;
    }
    th { background: #f6f8fa; font-weight: 600; }
    img {
      max-width: 100%;
      max-height: 560px;
      height: auto;
      display: block;
      margin: 8px auto;
      border: 1px solid #e5e7eb;
      border-radius: 4px;
      page-break-inside: avoid;
    }
    hr {
      border: none;
      border-top: 1px solid #d8dee4;
      margin: 16px 0;
    }
    a { color: #0969da; text-decoration: none; }
  </style>
</head>
<body>
${bodyHtml}
</body>
</html>`;

fs.writeFileSync(htmlPath, html, "utf8");

const browser = await puppeteer.launch({ headless: true });
const page = await browser.newPage();
await page.goto(`file://${htmlPath}`, { waitUntil: "networkidle0" });
await page.pdf({
  path: pdfPath,
  format: "A4",
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: "<div></div>",
  footerTemplate: '<div style="font-size:8pt;width:100%;text-align:center;color:#656d76;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
  margin: { top: "14mm", bottom: "16mm", left: "12mm", right: "12mm" }
});
await browser.close();
console.log("PDF saved:", pdfPath);
