const STAGE_LABELS = {
  parsing: 'Parsing',
  chunking: 'Chunking',
  enriching: 'Enriching',
  embedding: 'Embedding',
  saving: 'Saving',
  merging_entities: 'Merging entities',
  merging_relationships: 'Merging relationships',
  done: 'Done',
};

const STAGE_WEIGHTS = {
  parsing: 5,
  chunking: 5,
  enriching: 45,
  embedding: 20,
  saving: 5,
  merging_entities: 10,
  merging_relationships: 10,
};

function stagePercent(stage, current, total) {
  if (stage === 'done') return 100;
  let cumulative = 0;
  for (const [s, w] of Object.entries(STAGE_WEIGHTS)) {
    if (s === stage) {
      const sub = total > 0 ? current / total : 1;
      return Math.min(Math.round(cumulative + w * sub), 100);
    }
    cumulative += w;
  }
  return 0;
}

const SettingsPage = {
  template: `
    <div>
      <!-- Sync progress banner -->
      <div v-if="hasSyncProgress" class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
        <div class="text-sm font-medium text-blue-700 mb-2">⏳ Sync in progress</div>
        <div v-for="(sp, ns) in progressData.syncs" :key="ns" class="mb-3 last:mb-0">
          <div class="flex items-center justify-between text-xs text-blue-600 mb-1">
            <span>{{ ns }}: file {{ sp.file_current }}/{{ sp.file_total }}</span>
            <span>{{ sp.percent }}%</span>
          </div>
          <div class="w-full bg-blue-100 rounded-full h-2">
            <div class="bg-blue-500 h-2 rounded-full transition-all" :style="{ width: sp.percent + '%' }"></div>
          </div>
          <div v-for="(dp, dk) in sp.documents" :key="dk" class="mt-1 ml-3 text-xs text-blue-500">
            📄 {{ dp.doc_name }}: {{ stageLabel(dp.stage) }}
            <span v-if="dp.total > 0">({{ dp.current }}/{{ dp.total }})</span>
            — {{ docPercent(dp) }}%
          </div>
        </div>
        <!-- Standalone document progress (e.g. reindex) -->
        <div v-for="(dp, dk) in standaloneDocProgress" :key="dk" class="mb-2 last:mb-0">
          <div class="flex items-center justify-between text-xs text-blue-600 mb-1">
            <span>📄 {{ dp.doc_name }}: {{ stageLabel(dp.stage) }}
              <span v-if="dp.total > 0">({{ dp.current }}/{{ dp.total }})</span>
            </span>
            <span>{{ docPercent(dp) }}%</span>
          </div>
          <div class="w-full bg-blue-100 rounded-full h-1.5">
            <div class="bg-blue-500 h-1.5 rounded-full transition-all" :style="{ width: docPercent(dp) + '%' }"></div>
          </div>
        </div>
      </div>

      <div class="bg-white rounded-lg shadow-sm border p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm font-medium text-gray-600">Namespaces</div>
          <button @click="triggerSync" :disabled="syncing || hasSyncProgress"
            class="px-3 py-1.5 text-sm rounded border bg-green-50 border-green-300 hover:bg-green-100 disabled:opacity-50 disabled:cursor-not-allowed">
            {{ syncing ? '⏳ Starting...' : hasSyncProgress ? '⏳ Syncing...' : '🔄 Sync Google Drive' }}
          </button>
        </div>
        <div class="flex flex-wrap gap-2">
          <button v-for="ns in namespaces" :key="ns.name"
            @click="selectNamespace(ns.name)"
            class="px-3 py-1 text-sm rounded-full cursor-pointer transition"
            :class="selectedNs === ns.name ? 'tag-selected' : 'tag-unselected'">
            {{ ns.name }} <span class="opacity-70">({{ ns.document_count || 0 }})</span>
          </button>
        </div>
      </div>

      <div v-if="selectedNs" class="bg-white rounded-lg shadow-sm border p-4">
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm font-medium text-gray-600">Documents in <span class="text-blue-500">{{ selectedNs }}</span></div>
          <div v-if="loading" class="text-xs text-gray-400">Loading...</div>
        </div>
        <div v-if="documents.length === 0 && !loading" class="text-sm text-gray-400">No documents found.</div>
        <table v-else class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 border-b">
              <th class="pb-2 font-medium">Name</th>
              <th class="pb-2 font-medium w-32 text-center">ID</th>
              <th class="pb-2 font-medium w-20 text-center">Chunks</th>
              <th class="pb-2 font-medium w-20 text-center">Size</th>
              <th class="pb-2 font-medium w-28 text-center">Status</th>
              <th class="pb-2 font-medium w-36 text-right">Last Synced</th>
              <th class="pb-2 font-medium w-24 text-center">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="doc in documents" :key="doc.id" class="border-b last:border-0 hover:bg-gray-50">
              <td class="py-2 pr-2 truncate" style="max-width:260px" :title="doc.name">
                <a v-if="doc.url" :href="doc.url" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">{{ doc.name }}</a>
                <span v-else>{{ doc.name }}</span>
              </td>
              <td class="py-2 text-center">
                <code class="text-xs text-gray-400 bg-gray-50 px-1 py-0.5 rounded select-all" :title="doc.id">{{ doc.id }}</code>
              </td>
              <td class="py-2 text-center text-gray-500">{{ doc.chunks }}</td>
              <td class="py-2 text-center text-gray-500">{{ formatSize(doc.file_size) }}</td>
              <td class="py-2 text-center">
                <span class="px-2 py-0.5 rounded text-xs"
                  :class="doc._reindexStatus === 'success' ? 'bg-green-100 text-green-700' : doc._reindexStatus === 'error' ? 'bg-red-100 text-red-700' : doc._reindexStatus === 'loading' ? 'bg-yellow-100 text-yellow-700' : doc.status === 'processing' ? 'bg-yellow-100 text-yellow-700' : doc.status === 'error' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'">
                  {{ doc._reindexStatus === 'loading' ? 'Reindexing...' : doc._reindexStatus === 'success' ? 'Reindexed' : doc._reindexStatus === 'error' ? 'Error' : doc.status === 'processing' ? 'Processing...' : doc.status }}
                </span>
              </td>
              <td class="py-2 text-right text-gray-400 text-xs">{{ formatTime(doc.last_synced) }}</td>
              <td class="py-2 text-center">
                <button @click="reindex(doc)" :disabled="doc._reindexStatus === 'loading'"
                  class="px-2 py-1 text-xs border rounded hover:bg-blue-50 hover:border-blue-300 disabled:opacity-50 disabled:cursor-not-allowed">
                  🔄 Reindex
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Oversized files section -->
      <div class="bg-white rounded-lg shadow-sm border p-4 mt-4">
        <div class="flex items-center justify-between mb-3">
          <div class="text-sm font-medium text-gray-600">
            Oversized Files
            <span v-if="oversized" class="text-xs text-gray-400 font-normal">
              (limit: {{ oversized.max_file_size_mb }} MB)
            </span>
          </div>
          <button @click="loadOversized" :disabled="oversizedLoading"
            class="px-3 py-1.5 text-sm rounded border bg-gray-50 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed">
            {{ oversizedLoading ? '⏳ Scanning...' : '🔍 Scan' }}
          </button>
        </div>
        <div v-if="oversizedError" class="text-sm text-red-500">{{ oversizedError }}</div>
        <div v-else-if="!oversized && !oversizedLoading" class="text-sm text-gray-400">
          Click Scan to list files exceeding the size limit.
        </div>
        <div v-else-if="oversized && oversized.files.length === 0" class="text-sm text-gray-400">
          No oversized files found.
        </div>
        <table v-else-if="oversized" class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 border-b">
              <th class="pb-2 font-medium">Name</th>
              <th class="pb-2 font-medium w-32">Namespace</th>
              <th class="pb-2 font-medium w-24 text-right">Size</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in oversized.files" :key="f.id" class="border-b last:border-0 hover:bg-gray-50">
              <td class="py-2 pr-2 truncate" style="max-width:340px" :title="f.name">
                <a v-if="f.url" :href="f.url" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">{{ f.name }}</a>
                <span v-else>{{ f.name }}</span>
              </td>
              <td class="py-2 text-gray-500">{{ f.namespace }}</td>
              <td class="py-2 text-right text-gray-500">{{ formatSize(f.size) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- System Stats section -->
      <div class="bg-white rounded-lg shadow-sm border p-4 mt-4">
        <div class="text-sm font-medium text-gray-600 mb-3">System Stats</div>
        <div v-if="stats" class="space-y-2 text-sm">
          <div>Documents: <span class="font-medium">{{ stats.total_documents }}</span></div>
          <div>Chunks: <span class="font-medium">{{ stats.total_chunks }}</span></div>
          <div>RAM: <span class="font-medium">{{ stats.ram_usage_mb }} MB</span></div>
          <div>Uptime: <span class="font-medium">{{ formatUptime(stats.uptime_seconds) }}</span></div>
          <hr class="my-2">
          <div v-for="ns in stats.namespaces" :key="ns.name" class="text-xs">
            <div class="font-medium">{{ ns.name }}</div>
            <div class="text-gray-400 ml-2">{{ ns.documents }} docs, {{ ns.chunks }} chunks, {{ ns.entities }} entities</div>
          </div>
        </div>
        <div v-else class="text-sm text-gray-400">Loading...</div>
      </div>
    </div>
  `,
  data() {
    return {
      namespaces: [],
      selectedNs: null,
      documents: [],
      loading: false,
      progressData: { syncs: {}, documents: {} },
      progressTimer: null,
      stats: null,
      syncing: false,
      oversized: null,
      oversizedLoading: false,
      oversizedError: '',
    };
  },
  computed: {
    hasSyncProgress() {
      return Object.keys(this.progressData.syncs).length > 0
        || Object.keys(this.progressData.documents).length > 0;
    },
    standaloneDocProgress() {
      const syncDocKeys = new Set();
      for (const sp of Object.values(this.progressData.syncs)) {
        for (const dk of Object.keys(sp.documents || {})) syncDocKeys.add(dk);
      }
      const result = {};
      for (const [dk, dp] of Object.entries(this.progressData.documents)) {
        if (!syncDocKeys.has(dk)) result[dk] = dp;
      }
      return result;
    },
  },
  async mounted() {
    await this.loadNamespaces();
    this.startProgressPolling();
    this.loadStats();
  },
  beforeUnmount() {
    this.stopProgressPolling();
  },
  methods: {
    formatTime: SharedUtils.formatTime,
    formatUptime: SharedUtils.formatUptime,
    formatSize(bytes) {
      if (!bytes) return '—';
      const mb = bytes / (1024 * 1024);
      return mb < 0.01 ? '< 0.01 MB' : mb.toFixed(2) + ' MB';
    },
    stageLabel(stage) { return STAGE_LABELS[stage] || stage; },
    docPercent(dp) { return stagePercent(dp.stage, dp.current, dp.total); },
    startProgressPolling() {
      this.progressTimer = setInterval(() => this.pollProgress(), 2000);
    },
    stopProgressPolling() {
      if (this.progressTimer) { clearInterval(this.progressTimer); this.progressTimer = null; }
    },
    async pollProgress() {
      try {
        const resp = await SharedUtils.apiFetch('/progress');
        if (resp && resp.ok) {
          const prev = this.progressData;
          const next = await resp.json();
          this.progressData = next;
          const prevDocKeys = new Set(Object.keys(prev.documents || {}));
          for (const sp of Object.values(prev.syncs || {})) {
            for (const dk of Object.keys(sp.documents || {})) prevDocKeys.add(dk);
          }
          const nextDocKeys = new Set(Object.keys(next.documents || {}));
          for (const sp of Object.values(next.syncs || {})) {
            for (const dk of Object.keys(sp.documents || {})) nextDocKeys.add(dk);
          }
          if (prevDocKeys.size > 0 && this.selectedNs) {
            for (const k of prevDocKeys) {
              if (!nextDocKeys.has(k)) {
                await this.loadDocuments();
                break;
              }
            }
          }
        }
      } catch (e) { /* ignore */ }
    },
    async loadNamespaces() {
      try {
        const resp = await SharedUtils.apiFetch('/namespaces/');
        if (resp && resp.ok) {
          this.namespaces = await resp.json();
          if (this.namespaces.length > 0) this.selectNamespace(this.namespaces[0].name);
        }
      } catch (e) { console.error(e); }
    },
    async selectNamespace(name) {
      this.selectedNs = name;
      await this.loadDocuments();
    },
    async loadDocuments() {
      this.loading = true;
      this.documents = [];
      try {
        const resp = await SharedUtils.apiFetch('/documents/?namespace=' + encodeURIComponent(this.selectedNs));
        if (resp && resp.ok) {
          const docs = await resp.json();
          this.documents = docs.map(d => ({ ...d, _reindexStatus: null }));
        }
      } catch (e) { console.error(e); }
      this.loading = false;
    },
    async reindex(doc) {
      if (doc._reindexStatus === 'loading') return;
      doc._reindexStatus = 'loading';
      try {
        const resp = await SharedUtils.apiFetch(
          '/documents/' + encodeURIComponent(doc.id) + '/reindex?namespace=' + encodeURIComponent(this.selectedNs),
          { method: 'POST' }
        );
        if (resp && resp.ok) {
          const data = await resp.json();
          doc._reindexStatus = 'success';
          doc.chunks = data.chunks;
          doc.id = data.id;
          if (data.url) doc.url = data.url;
        } else {
          doc._reindexStatus = 'error';
        }
      } catch (e) {
        console.error(e);
        doc._reindexStatus = 'error';
      }
      setTimeout(() => { doc._reindexStatus = null; }, 3000);
    },
    async loadStats() {
      try {
        const resp = await SharedUtils.apiFetch('/stats');
        if (resp && resp.ok) this.stats = await resp.json();
      } catch (e) { console.error(e); }
    },
    async triggerSync() {
      this.syncing = true;
      try {
        await SharedUtils.apiFetch('/sync', { method: 'POST' });
      } catch (e) { console.error(e); }
      setTimeout(() => { this.syncing = false; }, 2000);
    },
    async loadOversized() {
      this.oversizedLoading = true;
      this.oversizedError = '';
      try {
        const resp = await SharedUtils.apiFetch('/oversized-files');
        if (resp && resp.ok) {
          this.oversized = await resp.json();
        } else {
          this.oversizedError = 'Failed to scan oversized files.';
        }
      } catch (e) {
        console.error(e);
        this.oversizedError = 'Failed to scan oversized files.';
      }
      this.oversizedLoading = false;
    },
  }
};
