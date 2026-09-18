function searchable(value) {
  return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function matches(row, query) {
  const text = searchable([row.title, row.artist, row.album, row.year, row.relativePath, row.filePath].join(" "));
  return searchable(query).trim().split(/\s+/).filter(Boolean).every((word) => text.includes(word));
}

export function filterLibraryRows(report, { query = "", filter = "all" } = {}) {
  const duplicatePaths = new Set((report?.duplicates || []).flatMap((group) => group.files.map((file) => file.filePath)));
  return (report?.rows || [])
    .filter((row) => (filter !== "duplicates" || duplicatePaths.has(row.filePath))
      && (filter !== "issues" || row.status !== "pass") && matches(row, query))
    .sort((left, right) => `${left.artist || ""} ${left.title || ""}`.localeCompare(`${right.artist || ""} ${right.title || ""}`, undefined, { sensitivity: "base" })
      || String(left.relativePath || left.filePath).localeCompare(String(right.relativePath || right.filePath)));
}

export function filterLibraryDuplicateGroups(groups, query = "") {
  // Retain the whole group when one member matches, so versions can be compared.
  return (groups || []).filter((group) => group.files.some((file) => matches({ ...group, ...file }, query)));
}

export function libraryPage(rows, page = 0, size = 50) {
  const pageSize = Number.isInteger(size) && size > 0 ? size : 50;
  const count = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.max(0, Math.min(count - 1, Number.isInteger(page) ? page : 0));
  return { rows: rows.slice(current * pageSize, (current + 1) * pageSize), page: current, pages: count };
}
