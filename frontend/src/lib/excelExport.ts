import type { ExtractionFieldValue } from "../types";
import type { FieldDefinitionItem } from "./api";

const MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

interface WorksheetCell {
  ref: string;
  value: string | number;
  style: number;
  type: "text" | "number";
}

export interface BatchExtractionWorkbookRow {
  fileName: string;
  status: string;
  error: string;
  results: ExtractionFieldValue[] | null;
}

export function buildExtractionResultsWorkbook(
  fields: FieldDefinitionItem[],
  results: ExtractionFieldValue[],
): Blob {
  const resultByKey = new Map(results.map((result) => [result.field_key, result]));
  const headers = ["序号", ...fields.map((field) => field.field_name)];
  const values = [
    1,
    ...fields.map((field) => {
      const result = resultByKey.get(field.field_key);
      return result?.status === "found" ? result.value : "";
    }),
    ];
  
    const sheetXml = buildSheetXml(headers, [values]);
    const now = new Date().toISOString();
  const files: Record<string, string> = {
    "[Content_Types].xml": buildContentTypesXml(),
    "_rels/.rels": buildRootRelsXml(),
    "docProps/core.xml": buildCorePropsXml(now),
    "docProps/app.xml": buildAppPropsXml(),
    "xl/workbook.xml": buildWorkbookXml(),
    "xl/_rels/workbook.xml.rels": buildWorkbookRelsXml(),
    "xl/styles.xml": buildStylesXml(),
    "xl/worksheets/sheet1.xml": sheetXml,
  };

  const xlsxBytes = zipStore(files);
  const xlsxBuffer = xlsxBytes.buffer.slice(
    xlsxBytes.byteOffset,
    xlsxBytes.byteOffset + xlsxBytes.byteLength,
  ) as ArrayBuffer;
  return new Blob([xlsxBuffer], { type: MIME_XLSX });
}

export function buildBatchExtractionResultsWorkbook(
  fields: FieldDefinitionItem[],
  rows: BatchExtractionWorkbookRow[],
): Blob {
  const headers = ["序号", "文件名", ...fields.map((field) => field.field_name), "处理状态", "错误信息"];
  const values = rows.map((row, index) => {
    const resultByKey = new Map((row.results ?? []).map((result) => [result.field_key, result]));
    return [
      index + 1,
      row.fileName,
      ...fields.map((field) => {
        const result = resultByKey.get(field.field_key);
        return result?.status === "found" ? result.value : "";
      }),
      workbookStatusLabel(row.status),
      row.error || "",
    ];
  });

  const sheetXml = buildSheetXml(headers, values);
  const now = new Date().toISOString();
  const files: Record<string, string> = {
    "[Content_Types].xml": buildContentTypesXml(),
    "_rels/.rels": buildRootRelsXml(),
    "docProps/core.xml": buildCorePropsXml(now),
    "docProps/app.xml": buildAppPropsXml(),
    "xl/workbook.xml": buildWorkbookXml(),
    "xl/_rels/workbook.xml.rels": buildWorkbookRelsXml(),
    "xl/styles.xml": buildStylesXml(),
    "xl/worksheets/sheet1.xml": sheetXml,
  };

  const xlsxBytes = zipStore(files);
  const xlsxBuffer = xlsxBytes.buffer.slice(
    xlsxBytes.byteOffset,
    xlsxBytes.byteOffset + xlsxBytes.byteLength,
  ) as ArrayBuffer;
  return new Blob([xlsxBuffer], { type: MIME_XLSX });
}

export function downloadExtractionResultsWorkbook(
  fileName: string,
  fields: FieldDefinitionItem[],
  results: ExtractionFieldValue[],
): void {
  const blob = buildExtractionResultsWorkbook(fields, results);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${fileName.replace(/\.[^.]+$/, "")}_提取结果.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadBatchExtractionResultsWorkbook(
  fileName: string,
  fields: FieldDefinitionItem[],
  rows: BatchExtractionWorkbookRow[],
): void {
  const blob = buildBatchExtractionResultsWorkbook(fields, rows);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${fileName.replace(/\.[^.]+$/, "")}_提取结果.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

function buildSheetXml(headers: Array<string | number>, rows: Array<Array<string | number>>): string {
  const colCount = headers.length;
  const lastCol = columnName(colCount);
  const cellsRow1: WorksheetCell[] = headers.map((value, index) => ({
    ref: `${columnName(index + 1)}1`,
    value,
    style: index === 0 ? 1 : 2,
    type: "text",
  }));
  const bodyRows = rows.map((values, rowIndex) => ({
    rowNumber: rowIndex + 2,
    cells: values.map((value, index) => ({
      ref: `${columnName(index + 1)}${rowIndex + 2}`,
      value,
      style: index === 0 ? 1 : 3,
      type: index === 0 ? "number" : "text",
    }) satisfies WorksheetCell),
  }));
  const lastRow = Math.max(2, rows.length + 1);

  return xmlDocument(`
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:${lastCol}${lastRow}"/>
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  ${buildColumnXml(headers, rows)}
  <sheetData>
    <row r="1" ht="42" customHeight="1">${cellsRow1.map(cellXml).join("")}</row>
    ${bodyRows.map((row) => `<row r="${row.rowNumber}" ht="108" customHeight="1">${row.cells.map(cellXml).join("")}</row>`).join("")}
  </sheetData>
  <pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>
</worksheet>`);
}

function buildColumnXml(headers: Array<string | number>, rows: Array<Array<string | number>>): string {
  const cols = headers.map((header, index) => {
    const colIndex = index + 1;
    let width = 14;
    if (index === 0) {
      width = 12;
    } else if (index === 1) {
      width = 24;
    } else {
      const textWidth = Math.max(
        displayWidth(String(header)),
        ...rows.map((values) => displayWidth(String(values[index] || ""))),
      );
      width = clamp(Math.ceil(textWidth * 0.75), 12, 20);
    }
    return `<col min="${colIndex}" max="${colIndex}" width="${width}" customWidth="1"/>`;
  });
  return `<cols>${cols.join("")}</cols>`;
}

function workbookStatusLabel(status: string): string {
  if (status === "completed") return "完成";
  if (status === "failed") return "失败";
  if (status === "skipped") return "跳过";
  if (status === "processing") return "处理中";
  return "未处理";
}

function cellXml(cell: WorksheetCell): string {
  if (cell.type === "number") {
    return `<c r="${cell.ref}" s="${cell.style}"><v>${cell.value}</v></c>`;
  }
  const text = String(cell.value ?? "");
  return `<c r="${cell.ref}" t="inlineStr" s="${cell.style}"><is><t>${escapeXml(text)}</t></is></c>`;
}

function buildStylesXml(): string {
  return xmlDocument(`
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="10"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="10"/><name val="Microsoft YaHei"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFFF00"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF00A9D6"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFC8E6C9"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FF000000"/></left>
      <right style="thin"><color rgb="FF000000"/></right>
      <top style="thin"><color rgb="FF000000"/></top>
      <bottom style="thin"><color rgb="FF000000"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>`);
}

function buildContentTypesXml(): string {
  return xmlDocument(`
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>`);
}

function buildRootRelsXml(): string {
  return xmlDocument(`
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`);
}

function buildWorkbookRelsXml(): string {
  return xmlDocument(`
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`);
}

function buildWorkbookXml(): string {
  return xmlDocument(`
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="提取结果" sheetId="1" r:id="rId1"/></sheets>
</workbook>`);
}

function buildCorePropsXml(createdAt: string): string {
  return xmlDocument(`
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>合同提取结果</dc:title>
  <dc:creator>rixin-contract-extract</dc:creator>
  <cp:lastModifiedBy>rixin-contract-extract</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">${createdAt}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">${createdAt}</dcterms:modified>
</cp:coreProperties>`);
}

function buildAppPropsXml(): string {
  return xmlDocument(`
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>rixin-contract-extract</Application>
</Properties>`);
}

function xmlDocument(body: string): string {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>${body.trim()}`;
}

function columnName(index: number): string {
  let name = "";
  let value = index;
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
}

function escapeXml(value: string): string {
  return value
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function displayWidth(value: string): number {
  return Array.from(value).reduce((total, char) => total + (/[\u3000-\u9fff\uff00-\uffef]/.test(char) ? 2 : 1), 0);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function zipStore(files: Record<string, string>): Uint8Array {
  const encoder = new TextEncoder();
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;

  Object.entries(files).forEach(([name, content]) => {
    const nameBytes = encoder.encode(name);
    const data = encoder.encode(content);
    const crc = crc32(data);
    const localHeader = new Uint8Array(30 + nameBytes.length);
    const local = new DataView(localHeader.buffer);
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0, true);
    local.setUint16(8, 0, true);
    local.setUint16(10, 0, true);
    local.setUint16(12, 0, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, nameBytes.length, true);
    localHeader.set(nameBytes, 30);
    localParts.push(localHeader, data);

    const centralHeader = new Uint8Array(46 + nameBytes.length);
    const central = new DataView(centralHeader.buffer);
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(4, 20, true);
    central.setUint16(6, 20, true);
    central.setUint16(8, 0, true);
    central.setUint16(10, 0, true);
    central.setUint16(12, 0, true);
    central.setUint16(14, 0, true);
    central.setUint32(16, crc, true);
    central.setUint32(20, data.length, true);
    central.setUint32(24, data.length, true);
    central.setUint16(28, nameBytes.length, true);
    central.setUint32(42, offset, true);
    centralHeader.set(nameBytes, 46);
    centralParts.push(centralHeader);

    offset += localHeader.length + data.length;
  });

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(8, Object.keys(files).length, true);
  endView.setUint16(10, Object.keys(files).length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, offset, true);

  return concatUint8Arrays([...localParts, ...centralParts, end]);
}

function concatUint8Arrays(parts: Uint8Array[]): Uint8Array {
  const totalLength = parts.reduce((sum, part) => sum + part.length, 0);
  const output = new Uint8Array(totalLength);
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
