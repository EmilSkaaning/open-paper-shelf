// Auto-saves this viewer's highlight annotations to the Open Paper Shelf
// backend as they're made, so the user never has to manually download and
// re-upload the annotated PDF. Loaded by viewer.html alongside viewer.mjs.
//
// lib_id/pid identify which paper to save to; they're passed as query
// params on the viewer URL (see frontend/app.py's iframe src).

const AUTOSAVE_DEBOUNCE_MS = 1500;
const AUTOSAVE_RETRY_MS = 5000;

const params = new URLSearchParams(window.location.search);
const libId = params.get("libId");
const pid = params.get("pid");

if (!libId || !pid) {
  console.warn("open-paper-shelf autosave: missing libId/pid, autosave disabled.");
} else {
  const saveUrl = `/papers/${encodeURIComponent(libId)}/${encodeURIComponent(pid)}/edited`;
  let debounceTimer = null;
  let retryTimer = null;
  let saveInFlight = false;
  let saveAgainAfterInFlight = false;

  async function doSave() {
    const app = window.PDFViewerApplication;
    if (!app?.pdfDocument) {
      return;
    }
    if (saveInFlight) {
      saveAgainAfterInFlight = true;
      return;
    }
    saveInFlight = true;
    try {
      const data = await app.pdfDocument.saveDocument();
      const response = await fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: data,
      });
      if (!response.ok) {
        console.error(
          `open-paper-shelf autosave: save failed (${response.status})`,
          await response.text().catch(() => "")
        );
        // 4xx means the request itself is invalid (e.g. oversized/corrupt
        // PDF) and will fail identically on retry, so only retry on
        // transient server-side failures.
        if (response.status >= 500) {
          scheduleRetry();
        }
      } else {
        // annotationStorage's #modified flag only re-fires onSetModified on
        // a false->true transition, so it must be reset on every successful
        // save - otherwise a later edit after a *failed* save would never
        // schedule another attempt.
        app.pdfDocument.annotationStorage.resetModified();
      }
    } catch (err) {
      console.error("open-paper-shelf autosave: save failed", err);
      scheduleRetry();
    } finally {
      saveInFlight = false;
      if (saveAgainAfterInFlight) {
        saveAgainAfterInFlight = false;
        scheduleSave();
      }
    }
  }

  function scheduleSave() {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(doSave, AUTOSAVE_DEBOUNCE_MS);
  }

  function scheduleRetry() {
    // Independent of scheduleSave/onSetModified: a failed save must keep
    // retrying even if the user makes no further edits (annotationStorage
    // won't re-signal modification on its own once #modified is stuck true).
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
    }
    retryTimer = setTimeout(() => {
      retryTimer = null;
      doSave();
    }, AUTOSAVE_RETRY_MS);
  }

  function attachToDocument() {
    const app = window.PDFViewerApplication;
    const annotationStorage = app?.pdfDocument?.annotationStorage;
    if (!annotationStorage) {
      return;
    }
    // Chain rather than overwrite: viewer.mjs's own onSetModified wires up
    // the native "unsaved changes" beforeunload warning, which must keep
    // firing even though we're also autosaving on a debounce.
    const existing = annotationStorage.onSetModified;
    annotationStorage.onSetModified = () => {
      if (typeof existing === "function") {
        existing();
      }
      scheduleSave();
    };
  }

  window.PDFViewerApplication.initializedPromise.then(() => {
    window.PDFViewerApplication.eventBus.on("documentloaded", attachToDocument);
    // Covers the case where the document already finished loading before
    // this listener was attached.
    attachToDocument();
  });

  window.addEventListener("beforeunload", () => {
    if (debounceTimer !== null) {
      // Best-effort: fire the pending save immediately so it isn't lost.
      clearTimeout(debounceTimer);
      doSave();
    }
  });
}
