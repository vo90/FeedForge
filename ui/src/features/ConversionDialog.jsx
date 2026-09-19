import { useEffect, useRef } from "react";

export default function ConversionDialog({ request, finish, setup }) {
  const dialog = useRef(null);
  useEffect(() => { dialog.current.showModal(); }, []);
  const { error } = request;
  return <dialog ref={dialog} className="conversion-dialog" aria-labelledby="conversion-title" onCancel={event => { event.preventDefault(); finish(null); }}>
    <h2 id="conversion-title">Stem splitting is unavailable</h2>
    <p>Stem splitting is enabled in Settings, but the selected server is not ready. You can set it up or continue with the full mix for this conversion.</p>
    <p className="dialog-error">{error || "The stem server isn’t ready. Open Tools · stems to install or start it."}</p>
    <div className="dialog-actions">
      <button autoFocus onClick={() => finish(null)}>Cancel</button>
      <button onClick={setup}>Set up stems</button>
      <button className="primary" onClick={() => finish(false)}>Continue with full mix</button>
    </div>
  </dialog>;
}
