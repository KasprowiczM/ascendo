// The Rust shell opens the WebView directly at http://127.0.0.1:<port>/
// via WebviewUrl::External, so this fallback page should never paint.
// If it does (e.g. WebView fell back to bundled assets), retry once after
// a short delay — by then the sidecar is almost certainly up.
setTimeout(() => {
  // eslint-disable-next-line no-restricted-globals
  location.reload();
}, 1000);
