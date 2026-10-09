'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
const LOCK_ERRORS = new Set(['EPERM', 'EACCES', 'EBUSY']);
const after = (result, next) => result?.then ? result.then(next) : next();

// Preserve synchronous admission when there is no contention. Only transient
// replacement failures yield; later snapshots cannot overtake a waiting write.
class HistoryWriter {
  constructor({ io = fs, delays = [50, 100, 200, 400, 800], sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
    Object.assign(this, { io, delays, sleep });
    this.pending = null; this.failure = null;
  }
  write(filename, value) {
    if (this.failure) throw this.failure;
    const bytes = JSON.stringify(value);
    const save = () => this._replace(filename, bytes);
    let result;
    try { result = this.pending ? this.pending.then(save) : save(); }
    catch (error) { this.failure = error; throw error; }
    if (!result?.then) return;
    const pending = result.catch(error => { this.failure = error; throw error; }).finally(() => {
      if (this.pending === pending) this.pending = null;
    });
    this.pending = pending;
    // Progress callbacks cannot await. flush() still propagates their failure.
    pending.catch(() => {});
    return pending;
  }
  _replace(filename, bytes) {
    const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
    const cleanup = () => { try { this.io.unlinkSync(temporary); } catch {} };
    try { this.io.writeFileSync(temporary, bytes, { flag: 'wx', mode: 0o600 }); }
    catch (error) { if (error.code !== 'EEXIST') cleanup(); throw error; }
    try { this.io.renameSync(temporary, filename); }
    catch (error) {
      if (!LOCK_ERRORS.has(error.code)) { cleanup(); throw error; }
      return (async () => {
        try {
          for (const delay of this.delays) {
            await this.sleep(delay);
            try { this.io.renameSync(temporary, filename); return; }
            catch (next) { error = next; if (!LOCK_ERRORS.has(error.code)) throw error; }
          }
          throw error;
        } finally { cleanup(); }
      })();
    }
  }
  async flush() { while (this.pending) await this.pending; if (this.failure) throw this.failure; }
}
module.exports = { HistoryWriter, after };
