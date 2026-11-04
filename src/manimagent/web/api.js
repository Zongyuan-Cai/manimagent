export function createApi(fetcher = globalThis.fetch) {
  async function request(url, options) {
    const response = await fetcher(url, options);
    const data = await response.json().catch(() => null);
    if (!response.ok || data === null) {
      throw Object.assign(new Error(data?.error?.message || data?.detail || (response.ok ? '服务返回了无效数据，请重试' : `请求失败（HTTP ${response.status}）`)), {
        status: response.status, retryable: data?.error?.retryable ?? !response.ok,
      });
    }
    return data;
  }
  const post = (url, data) => request(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  async function html(url) {
    const response = await fetcher(url);
    if (!response.ok) throw Object.assign(new Error('正文读取失败'), {status: response.status, retryable: response.status >= 500});
    return response.text();
  }
  return {request, post, html, health: () => request('/api/v2/health')};
}
