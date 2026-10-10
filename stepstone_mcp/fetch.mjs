// Ordinary identified, cookie-free HTTP GET. No redirect/challenge handling here:
// Python validates every destination against current provider robot policy.
const [url, rawTimeout, userAgent] = process.argv.slice(2);
const parsed = new URL(url);
if (parsed.protocol !== 'https:' || parsed.hostname !== 'www.stepstone.de' ||
    parsed.username || parsed.password || parsed.hash || (parsed.port && parsed.port !== '443')) {
  throw new Error('Invalid public StepStone URL');
}
const timeout = Number(rawTimeout);
if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 60000) throw new Error('Invalid timeout');
try {
  const response = await fetch(url, {
    redirect: 'manual', credentials: 'omit', headers: { 'User-Agent': userAgent },
    signal: AbortSignal.timeout(Math.ceil(timeout)),
  });
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body ?? []) {
    length += chunk.length;
    if (length > 8 * 1024 * 1024) {
      throw new Error('response_too_large');
    }
    chunks.push(Buffer.from(chunk));
  }
  process.stdout.write(JSON.stringify({
    status: response.status, headers: Object.fromEntries(response.headers),
    body: Buffer.concat(chunks).toString('utf8'),
  }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    error: error.name === 'TimeoutError' || error.name === 'AbortError' ? 'timeout' :
      error.message === 'response_too_large' ? 'response_too_large' : 'network_error',
    exception_type: error.name,
  }));
  process.exitCode = 2;
}
