const StatsPage = {
  template: `
    <div class="bg-white rounded-lg shadow-sm border p-4">
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
  `,
  data() {
    return { stats: null };
  },
  mounted() {
    this.loadStats();
  },
  methods: {
    formatUptime: SharedUtils.formatUptime,
    async loadStats() {
      try {
        const resp = await SharedUtils.apiFetch('/stats');
        if (resp && resp.ok) this.stats = await resp.json();
      } catch (e) { console.error(e); }
    },
  }
};
