// Preview the shared publisher's naming convention; it assigns collision suffixes.
export function safeOutputSegment(value, fallback = "converted") {
  const clean = candidate => String(candidate || "").normalize("NFC")
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/\s+/g, " ").trim()
    .replace(/[. ]+$/g, "").slice(0, 120).replace(/[. ]+$/g, "");
  const result = clean(value) || clean(fallback) || "converted";
  return /^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(result) ? `_${result}` : result;
}

export function editorOutputName(form, tracks = [], settings = {}) {
  const artist = String(form.artist || "Unknown Artist").trim();
  const title = String(form.title || "Untitled").trim();
  const source = safeOutputSegment(`${artist} - ${title}`);
  const labels = tracks.filter(track => track.selected).map(track => String(track.role || track.type || "guitar").toLowerCase());
  const parts = [["bass", "B"], ["lead", "L"], ["rhythm", "R"], ["vocal", "V"], ["combo", "C"]]
    .filter(([name]) => labels.some(label => label.includes(name))).map(([, code]) => code).join("");
  const values = { artist, title, source, album: form.album || "", year: form.year || "", parts };
  const rendered = String(settings.nameTemplate || "{source}")
    .replace(/\{(artist|title|album|year|source|parts)\}/gi, (_, key) => values[key.toLowerCase()])
    .replace(/(?:\s+-\s*){2,}/g, " - ").replace(/^[ \-_.]+|[ \-_.]+$/g, "");
  return `${safeOutputSegment(rendered || title || source, source)}.feedpak`;
}
