const SENSITIVE_VALUE_FLAGS = new Set([
  "--demucs-api-key"
]);

function redactConverterArgs(args = []) {
  const redacted = [];
  let redactNext = false;
  for (const rawValue of args) {
    const value = String(rawValue);
    if (redactNext) {
      redacted.push("[redacted]");
      redactNext = false;
      continue;
    }
    const equalsIndex = value.indexOf("=");
    const flag = equalsIndex >= 0 ? value.slice(0, equalsIndex) : value;
    if (!SENSITIVE_VALUE_FLAGS.has(flag)) {
      redacted.push(value);
      continue;
    }
    if (equalsIndex >= 0) {
      redacted.push(`${flag}=[redacted]`);
    } else {
      redacted.push(flag);
      redactNext = true;
    }
  }
  return redacted;
}

module.exports = { redactConverterArgs };
