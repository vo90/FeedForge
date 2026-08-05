function normalizeDuplicateText(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/['’`]/g, "")
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\b(remaster(?:ed)?|deluxe|explicit|clean|version|mono|stereo)\b/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function duplicateAuditKey(preview) {
  const title = normalizeDuplicateText(preview?.title);
  const artist = normalizeDuplicateText(preview?.artist);
  if (!title || !artist || artist === "unknown artist") return "";
  return `${artist}|${title}`;
}

function scoreDuplicateCandidate(row) {
  let score = 0;
  score += Number(row.arrangements || 0) * 10;
  score += Number(row.stems || 0) * 3;
  score += (row.authors || []).length * 5;
  score += (row.stemIds || []).includes("full") ? 10 : 0;
  score -= (row.missing || []).length * 2;
  score += Number(row.size || 0) / (1024 * 1024 * 100);
  return score;
}

function duplicateGroupsFromAuditRows(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    if (row.status === "error" || !row.duplicateKey) continue;
    const list = grouped.get(row.duplicateKey) || [];
    list.push(row);
    grouped.set(row.duplicateKey, list);
  }
  return [...grouped.values()]
    .filter((files) => files.length > 1)
    .map((files) => {
      const sorted = [...files].sort((left, right) => scoreDuplicateCandidate(right) - scoreDuplicateCandidate(left));
      const first = sorted[0];
      return {
        key: first.duplicateKey,
        artist: first.artist,
        title: first.title,
        album: first.album,
        year: first.year,
        count: sorted.length,
        files: sorted.map((row, index) => ({
          filePath: row.filePath,
          relativePath: row.relativePath,
          size: row.size || 0,
          album: row.album,
          year: row.year,
          duration: row.duration,
          stems: row.stems,
          stemIds: row.stemIds,
          arrangements: row.arrangements,
          authors: row.authors,
          status: row.status,
          missing: row.missing,
          recommended: index === 0,
          score: scoreDuplicateCandidate(row)
        }))
      };
    })
    .sort((left, right) => `${left.artist} ${left.title}`.localeCompare(`${right.artist} ${right.title}`, undefined, { sensitivity: "base" }));
}

module.exports = {
  duplicateAuditKey,
  duplicateGroupsFromAuditRows,
  normalizeDuplicateText,
  scoreDuplicateCandidate
};
