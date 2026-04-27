const SearchPage = {
  template: `
    <div>
      <div class="bg-white rounded-lg shadow-sm border p-4 mb-4">
        <div class="text-sm font-medium text-gray-600 mb-2">Namespaces</div>
        <div class="flex flex-wrap gap-2">
          <button v-for="ns in namespaces" :key="ns.name"
            @click="toggleNamespace(ns.name)"
            class="px-3 py-1 text-sm rounded-full cursor-pointer transition"
            :class="selectedNamespaces.includes(ns.name) ? 'tag-selected' : 'tag-unselected'">
            {{ ns.name }} <span class="opacity-70">({{ ns.document_count || 0 }})</span>
          </button>
          <button @click="toggleAll" class="px-3 py-1 text-sm rounded-full border border-gray-300 hover:bg-gray-100 cursor-pointer">
            {{ allSelected ? 'None' : 'All' }}
          </button>
        </div>
      </div>

      <div class="bg-white rounded-lg shadow-sm border p-4 mb-4">
        <div class="flex gap-4 mb-3">
          <label v-for="m in modes" :key="m" class="flex items-center gap-1 text-sm cursor-pointer">
            <input type="radio" :value="m" v-model="searchMode" class="text-blue-500"> {{ m }}
          </label>
          <select v-model.number="topK" class="ml-auto border rounded px-2 py-1 text-sm">
            <option v-for="k in [3,5,10,20]" :value="k">Top {{ k }}</option>
          </select>
        </div>
        <div class="flex gap-2">
          <input v-model="queryText" @keyup.enter="doSearch" placeholder="Enter your query..."
            class="flex-1 border rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300">
          <button @click="doSearch" :disabled="searching" class="px-6 py-2 bg-blue-500 text-white rounded-lg text-sm hover:bg-blue-600 disabled:opacity-50">
            {{ searching ? 'Searching...' : 'Search' }}
          </button>
        </div>
      </div>

      <div v-if="answer" class="bg-white rounded-lg shadow-sm border p-4 mb-4">
        <div class="text-sm font-medium text-gray-600 mb-2">Answer</div>
        <div class="text-sm text-gray-800 whitespace-pre-wrap">{{ answer }}</div>
      </div>

      <div v-if="results.length" class="mb-2 text-sm text-gray-500">
        {{ results.length }} chunks <span v-if="searchTime">({{ searchTime }}ms)</span>
      </div>

      <div v-for="(chunk, idx) in results" :key="chunk.chunk_id" class="bg-white rounded-lg shadow-sm border p-4 mb-3">
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-medium text-gray-700">
            #{{ idx + 1 }} <span class="text-blue-500">{{ chunk.namespace }}</span> / {{ chunk.source }}
            <span v-if="chunk.page" class="text-gray-400">(p.{{ chunk.page }})</span>
          </div>
          <div class="text-xs text-gray-400">score: {{ (chunk.score || 0).toFixed(3) }}</div>
        </div>
        <div class="text-sm text-gray-700 mb-3 leading-relaxed" style="max-height:200px;overflow-y:auto">{{ chunk.text }}</div>
        <div class="flex gap-2">
          <button @click="loadContext(chunk, -1)" class="px-2 py-1 text-xs border rounded hover:bg-gray-50">◀ Prev</button>
          <button @click="loadContext(chunk, 1)" class="px-2 py-1 text-xs border rounded hover:bg-gray-50">Next ▶</button>
          <button @click="loadSiblings(chunk)" class="px-2 py-1 text-xs border rounded hover:bg-gray-50">📄 Document</button>
        </div>
      </div>

      <div v-if="contextChunks.length" class="bg-white rounded-lg shadow-sm border p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm font-medium text-gray-600">Chunk Context — {{ contextDoc }}</div>
          <button @click="contextChunks = []" class="text-xs text-gray-400 hover:text-gray-600">✕ Close</button>
        </div>
        <div class="space-y-1">
          <div v-for="c in contextChunks" :key="c.chunk_id"
            class="p-2 rounded text-sm" :class="c.is_target ? 'chunk-target' : 'bg-gray-50'">
            <span class="text-xs text-gray-400">[{{ c.chunk_id.substring(0,12) }}]</span>
            <span v-if="c.page" class="text-xs text-gray-400">p.{{ c.page }}</span>
            <span v-if="c.is_target" class="text-xs font-bold text-amber-600">★ TARGET</span>
            <div class="mt-1 text-gray-700" style="max-height:100px;overflow-y:auto">{{ c.text }}</div>
          </div>
        </div>
        <div class="flex gap-2 mt-3">
          <button v-if="contextHasMoreBefore" @click="expandContext(-1)" class="px-2 py-1 text-xs border rounded hover:bg-gray-50">◀ Load more before</button>
          <button v-if="contextHasMoreAfter" @click="expandContext(1)" class="px-2 py-1 text-xs border rounded hover:bg-gray-50">Load more after ▶</button>
        </div>
      </div>
    </div>
  `,
  data() {
    return {
      namespaces: [],
      selectedNamespaces: [],
      modes: ['hybrid', 'local', 'global', 'mix', 'naive', 'bypass'],
      searchMode: window.DEFAULT_MODE || 'hybrid',
      topK: 5,
      queryText: '',
      searching: false,
      answer: '',
      results: [],
      searchTime: null,
      contextChunks: [],
      contextDoc: '',
      contextTargetId: '',
      contextHasMoreBefore: false,
      contextHasMoreAfter: false,
      contextBefore: 2,
      contextAfter: 2,
    };
  },
  computed: {
    allSelected() {
      return this.namespaces.length > 0 && this.selectedNamespaces.length === this.namespaces.length;
    }
  },
  async mounted() {
    await this.loadNamespaces();
  },
  methods: {
    async loadNamespaces() {
      try {
        const resp = await SharedUtils.apiFetch('/namespaces/');
        if (resp && resp.ok) {
          this.namespaces = await resp.json();
          this.selectedNamespaces = this.namespaces.map(n => n.name);
        }
      } catch (e) { console.error(e); }
    },
    toggleNamespace(name) {
      const idx = this.selectedNamespaces.indexOf(name);
      if (idx >= 0) this.selectedNamespaces.splice(idx, 1);
      else this.selectedNamespaces.push(name);
    },
    toggleAll() {
      if (this.allSelected) this.selectedNamespaces = [];
      else this.selectedNamespaces = this.namespaces.map(n => n.name);
    },
    async doSearch() {
      if (!this.queryText.trim()) return;
      this.searching = true;
      this.answer = '';
      this.results = [];
      this.contextChunks = [];
      const start = Date.now();
      try {
        const body = {
          query: this.queryText,
          namespaces: this.selectedNamespaces.length ? this.selectedNamespaces : null,
          mode: this.searchMode,
          top_k: this.topK,
        };
        const resp = await SharedUtils.apiFetch('/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (resp && resp.ok) {
          const data = await resp.json();
          this.answer = data.answer || '';
          this.results = data.chunks || [];
          this.searchTime = Date.now() - start;
        }
      } catch (e) { console.error(e); }
      this.searching = false;
    },
    async loadContext(chunk, direction) {
      const ns = chunk.namespace;
      const before = direction < 0 ? 3 : 1;
      const after = direction > 0 ? 3 : 1;
      try {
        const resp = await SharedUtils.apiFetch(
          `/chunks/${chunk.chunk_id}/context?namespace=${encodeURIComponent(ns)}&before=${before}&after=${after}`
        );
        if (resp && resp.ok) {
          const data = await resp.json();
          this.contextChunks = data.chunks || [];
          this.contextDoc = data.document || '';
          this.contextTargetId = chunk.chunk_id;
          this.contextHasMoreBefore = data.has_more_before;
          this.contextHasMoreAfter = data.has_more_after;
          this.contextBefore = before;
          this.contextAfter = after;
        }
      } catch (e) { console.error(e); }
    },
    async loadSiblings(chunk) {
      const ns = chunk.namespace;
      try {
        const resp = await SharedUtils.apiFetch(
          `/chunks/${chunk.chunk_id}/siblings?namespace=${encodeURIComponent(ns)}`
        );
        if (resp && resp.ok) {
          const data = await resp.json();
          this.contextChunks = data.chunks || [];
          this.contextDoc = data.document || '';
          this.contextTargetId = chunk.chunk_id;
          this.contextHasMoreBefore = false;
          this.contextHasMoreAfter = false;
        }
      } catch (e) { console.error(e); }
    },
    async expandContext(direction) {
      if (!this.contextTargetId) return;
      const ns = this.contextChunks[0] && this.contextChunks[0].namespace || '';
      if (direction < 0) this.contextBefore += 3;
      else this.contextAfter += 3;
      try {
        const resp = await SharedUtils.apiFetch(
          `/chunks/${this.contextTargetId}/context?namespace=${encodeURIComponent(ns)}&before=${this.contextBefore}&after=${this.contextAfter}`
        );
        if (resp && resp.ok) {
          const data = await resp.json();
          this.contextChunks = data.chunks || [];
          this.contextHasMoreBefore = data.has_more_before;
          this.contextHasMoreAfter = data.has_more_after;
        }
      } catch (e) { console.error(e); }
    },
  }
};
