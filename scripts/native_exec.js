/* Native tool adapter, evaluated in functions.exec (no filesystem or daemon).
 * The caller supplies the existing tools/load/store. State lives in that
 * conversation's store; after a context/host reset reconcile native receipts
 * and the original run before submitting anything. This is not a job queue.
 */
(function nativeExec({tools, load, store}) {
  const prefix = 'btc-native-exec-v1:';
  const copy = value => JSON.parse(JSON.stringify(value));
  const quote = value => "'" + value.replace(/'/g, "'\\''") + "'";
  function identity(request) {
    if (!request || typeof request.key !== 'string' || !request.key.trim()
        || !Array.isArray(request.argv) || !request.argv.length
        || request.argv.some(x => typeof x !== 'string' || x.includes('\0'))
        || !request.argv[0] || typeof request.workdir !== 'string'
        || !request.workdir.startsWith('/')) throw Error('key, argv and absolute workdir required');
    // Polling budgets do not change the identity of the actual invocation.
    return JSON.stringify([request.argv, request.workdir, request.sandbox_permissions || 'use_default']);
  }
  function receipt(value, reused) {
    return {key:value.key, state:value.state, session_id:value.session_id,
      exit_code:value.exit_code, reused, native:copy(value.native ?? null),
      error:value.error || null};
  }
  function save(value) { store(prefix + value.key, copy(value)); }
  function accept(value, raw) {
    value.error = null;
    value.native = copy(raw);
    value.history.push(copy(raw));
    if (raw && Number.isInteger(raw.exit_code)) {
      value.exit_code = raw.exit_code;
      value.state = raw.exit_code === 0 ? 'COMPLETED' : 'FAILED';
      value.session_id = null;
    } else if (raw && Number.isInteger(raw.session_id) && raw.session_id > 0) {
      value.session_id = raw.session_id;
      value.state = 'RUNNING';
    } else {
      value.state = 'UNKNOWN';
      value.error = 'native result has neither an exit code nor a usable session_id';
    }
    save(value);
    return receipt(value, false);
  }
  function unknown(value, error) {
    value.state = 'UNKNOWN';
    value.error = String(error && error.message || error);
    value.failures.push(value.error);
    save(value);
    return receipt(value, false);
  }
  async function submit(request) {
    const signature = identity(request);
    const prior = load(prefix + request.key);
    if (prior) {
      if (prior.signature !== signature) throw Error('same invocation key has different input');
      return receipt(prior, true);
    }
    const value = {key:request.key, signature, state:'STARTING', session_id:null,
      exit_code:null, native:null, history:[], failures:[], error:null};
    // Reserve synchronously before the first await, including concurrent callers.
    save(value);
    try {
      return accept(value, await tools.exec_command({
        cmd:'exec ' + request.argv.map(quote).join(' '), workdir:request.workdir,
        login:false, yield_time_ms:request.yield_time_ms ?? 1000,
        max_output_tokens:request.max_output_tokens ?? 2000,
        // Omission uses the host's policy; some hosts reject even an explicit
        // default override. Forward only a deliberately requested override.
        ...(request.sandbox_permissions && request.sandbox_permissions !== 'use_default'
          ? {sandbox_permissions:request.sandbox_permissions} : {}),
        ...(request.justification ? {justification:request.justification} : {})
      }));
    } catch (error) { return unknown(value, error); }
  }
  async function poll(key, options = {}) {
    const value = load(prefix + key);
    if (!value) throw Error('no original invocation; reconcile native records before starting');
    if (!['RUNNING', 'UNKNOWN'].includes(value.state) || !value.session_id)
      return receipt(value, true);
    value.state = 'POLLING';
    save(value);
    try {
      return accept(value, await tools.write_stdin({session_id:value.session_id,
        chars:'', yield_time_ms:options.yield_time_ms ?? 1000,
        max_output_tokens:options.max_output_tokens ?? 2000}));
    } catch (error) { return unknown(value, error); }
  }
  return {submit, poll, read:key => copy(load(prefix + key) ?? null)};
})
