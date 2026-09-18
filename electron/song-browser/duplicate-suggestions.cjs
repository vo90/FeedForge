'use strict';

// Suggestions never alter the conservative artist/title groups or checked choices.
// A sorted neighbourhood bounds work even for a 5,000-chart catalogue snapshot.
function possibleDuplicates(groups, { maxComparisons = 40000, maxSuggestions = 200 } = {}) {
  const fold = (value) => String(value || '').normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
  const words = (value) => fold(value).replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const edition = (value) => {
    const tokens = words(value).split(' ');
    return tokens.filter((token, index) => /^(?:live|acoustic|remix|medley|demo|instrumental|karaoke|cover|remaster(?:ed)?|version|edit|reprise|part|pt|vol(?:ume)?|\d+)$/.test(token)
      || /^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)$/.test(token) || /^(?:part|pt|vol(?:ume)?)$/.test(tokens[index - 1] || '')).join(' ');
  };
  const distance = (a, b) => {
    if (Math.abs(a.length - b.length) > 2) return 3;
    let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const row = [i];
      for (let j = 1; j <= b.length; j++) row[j] = Math.min(row[j - 1] + 1, previous[j] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (Math.min(...row) > 2) return 3;
      previous = row;
    }
    return previous[b.length];
  };
  const artists = new Map();
  for (const group of groups.slice(0, 5000)) {
    const key = fold(group.artist);
    if (!artists.has(key)) artists.set(key, []);
    artists.get(key).push({ group, title: words(group.title).slice(0, 300), edition: edition(group.title) });
  }
  let comparisons = 0;
  const suggestions = [];
  for (const rows of artists.values()) {
    rows.sort((a, b) => a.title.localeCompare(b.title));
    for (let i = 0; i < rows.length; i++) for (let j = i + 1; j < Math.min(rows.length, i + 13); j++) {
      if (++comparisons > maxComparisons || suggestions.length >= maxSuggestions) return { suggestions, comparisons: Math.min(comparisons, maxComparisons), limited: true };
      const a = rows[i], b = rows[j];
      if (a.edition !== b.edition || a.title.length < 4 || b.title.length < 4) continue;
      const punctuation = a.title === b.title;
      if (!punctuation && (Math.min(a.title.length, b.title.length) < 6 || distance(a.title, b.title) > (Math.min(a.title.length, b.title.length) < 10 ? 1 : 2))) continue;
      const groupKeys = [a.group.key, b.group.key].sort();
      const id = require('node:crypto').createHash('sha256').update(JSON.stringify(groupKeys)).digest('hex').slice(0, 24);
      suggestions.push({ id, groupKeys, reason: punctuation ? 'The titles differ only in punctuation or spacing.' : 'The titles have similar spelling. Compare the editions before choosing.' });
    }
  }
  return { suggestions, comparisons, limited: false };
}

module.exports = { possibleDuplicates };
