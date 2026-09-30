// Preloaded into supergateway (node --require). supergateway 3.4.3 throws
// "No connection established for request ID" from a stdout handler when a
// client drops its POST before the child's reply arrives, which kills the
// whole process. Log and keep serving instead.
process.on('uncaughtException', (err) => {
  console.error('[crash-guard] uncaught exception ignored:', err && err.message);
});
process.on('unhandledRejection', (err) => {
  console.error('[crash-guard] unhandled rejection ignored:', err && err.message);
});
