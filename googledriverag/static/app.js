const routes = [
  { path: '/', component: SearchPage },
  { path: '/api-history', component: ApiHistoryPage },

  { path: '/settings', component: SettingsPage },
];

const router = VueRouter.createRouter({
  history: VueRouter.createWebHashHistory(),
  routes,
});

const app = Vue.createApp({});
app.use(router);
app.mount('#app');
