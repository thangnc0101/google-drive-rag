const SharedUtils = {
  async apiFetch(url, options) {
    const resp = await fetch(url, { ...options, credentials: 'include' });
    if (resp.status === 401) {
      window.location.reload();
      return null;
    }
    return resp;
  },
  formatTime(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleString();
  },
  formatTokens(n) {
    if (!n) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  },
  formatUptime(seconds) {
    if (!seconds) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + s + 's';
    return s + 's';
  }
};
