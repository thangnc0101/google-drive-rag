const ApiHistoryPage = {
  template: `
    <div class="bg-white rounded-lg shadow-sm border p-4 mb-4">
      <div class="flex items-center justify-between mb-3">
        <div class="text-sm font-medium text-gray-600">API Call History</div>
        <div class="flex gap-2">
          <select v-model="filterType" @change="loadApiCalls()" class="border rounded px-2 py-1 text-sm">
            <option value="">All</option>
            <option value="llm">LLM</option>
            <option value="embedding">Embedding</option>
          </select>
          <input v-model="filterNamespace" @change="loadApiCalls()" placeholder="Namespace" class="border rounded px-2 py-1 text-sm w-32" />
          <button @click="clearApiCalls" class="px-3 py-1 text-sm rounded border border-red-300 text-red-600 hover:bg-red-50">Clear All</button>
        </div>
      </div>

      <div v-if="stats" class="grid grid-cols-5 gap-3 mb-4">
        <div class="bg-gray-50 rounded p-2 text-center">
          <div class="text-lg font-semibold">{{ stats.total_calls }}</div>
          <div class="text-xs text-gray-500">Total Calls</div>
        </div>
        <div class="bg-blue-50 rounded p-2 text-center">
          <div class="text-lg font-semibold text-blue-600">{{ stats.llm_calls }}</div>
          <div class="text-xs text-gray-500">LLM Calls</div>
        </div>
        <div class="bg-green-50 rounded p-2 text-center">
          <div class="text-lg font-semibold text-green-600">{{ stats.embedding_calls }}</div>
          <div class="text-xs text-gray-500">Embedding Calls</div>
        </div>
        <div class="bg-purple-50 rounded p-2 text-center">
          <div class="text-lg font-semibold text-purple-600">{{ formatTokens(stats.total_input_tokens) }}</div>
          <div class="text-xs text-gray-500">Input Tokens</div>
        </div>
        <div class="bg-yellow-50 rounded p-2 text-center">
          <div class="text-lg font-semibold text-yellow-600">{{ formatTokens(stats.total_output_tokens) }}</div>
          <div class="text-xs text-gray-500">Output Tokens</div>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b text-left text-gray-500">
              <th class="py-2 pr-3">Time</th>
              <th class="py-2 pr-3">Type</th>
              <th class="py-2 pr-3">Model</th>
              <th class="py-2 pr-3">Operation</th>
              <th class="py-2 pr-3">Namespace</th>
              <th class="py-2 pr-3">Document</th>
              <th class="py-2 pr-3 text-right">In Tokens</th>
              <th class="py-2 text-right">Out Tokens</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="call in calls" :key="call.id" class="border-b hover:bg-gray-50">
              <td class="py-2 pr-3 text-xs text-gray-400 whitespace-nowrap">{{ formatTime(call.created_at) }}</td>
              <td class="py-2 pr-3">
                <span class="px-2 py-0.5 rounded text-xs" :class="call.call_type === 'llm' ? 'bg-blue-100 text-blue-700' : 'bg-green-100 text-green-700'">{{ call.call_type }}</span>
              </td>
              <td class="py-2 pr-3 text-xs text-gray-600 max-w-xs truncate">{{ call.model }}</td>
              <td class="py-2 pr-3 text-xs">{{ call.operation }}</td>
              <td class="py-2 pr-3 text-xs text-indigo-600 max-w-xs truncate">{{ call.namespace || '-' }}</td>
              <td class="py-2 pr-3 text-xs text-gray-500 max-w-xs truncate">{{ call.document_name || '-' }}</td>
              <td class="py-2 pr-3 text-right text-xs">{{ call.input_tokens.toLocaleString() }}</td>
              <td class="py-2 text-right text-xs">{{ call.output_tokens.toLocaleString() }}</td>
            </tr>
            <tr v-if="!calls.length">
              <td colspan="8" class="py-8 text-center text-gray-400">No API calls recorded yet</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="calls.length >= limit" class="mt-3 text-center">
        <button @click="loadMore" class="px-4 py-1 text-sm border rounded hover:bg-gray-50">Load more</button>
      </div>
    </div>
  `,
  data() {
    return {
      calls: [],
      stats: null,
      filterType: '',
      filterNamespace: '',
      limit: 100,
      offset: 0,
    };
  },
  mounted() {
    this.loadApiCalls();
  },
  methods: {
    formatTime: SharedUtils.formatTime,
    formatTokens: SharedUtils.formatTokens,
    async loadApiCalls() {
      this.offset = 0;
      try {
        const params = new URLSearchParams({ limit: this.limit, offset: 0 });
        if (this.filterType) params.set('call_type', this.filterType);
        if (this.filterNamespace) params.set('namespace', this.filterNamespace);
        const resp = await SharedUtils.apiFetch('/api-calls/?' + params.toString());
        if (resp && resp.ok) this.calls = await resp.json();
        const statsResp = await SharedUtils.apiFetch('/api-calls/stats');
        if (statsResp && statsResp.ok) this.stats = await statsResp.json();
      } catch (e) { console.error(e); }
    },
    async loadMore() {
      this.offset += this.limit;
      try {
        const params = new URLSearchParams({ limit: this.limit, offset: this.offset });
        if (this.filterType) params.set('call_type', this.filterType);
        if (this.filterNamespace) params.set('namespace', this.filterNamespace);
        const resp = await SharedUtils.apiFetch('/api-calls/?' + params.toString());
        if (resp && resp.ok) {
          const more = await resp.json();
          this.calls = this.calls.concat(more);
        }
      } catch (e) { console.error(e); }
    },
    async clearApiCalls() {
      if (!confirm('Clear all API call history?')) return;
      try {
        const resp = await SharedUtils.apiFetch('/api-calls/', { method: 'DELETE' });
        if (resp && resp.ok) {
          this.calls = [];
          this.stats = { total_calls: 0, llm_calls: 0, embedding_calls: 0, total_input_tokens: 0, total_output_tokens: 0 };
        }
      } catch (e) { console.error(e); }
    },
  }
};
