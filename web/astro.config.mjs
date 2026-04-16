import { defineConfig } from 'astro/config';

// Integrations
import svelte from '@astrojs/svelte';
import partytown from '@astrojs/partytown';
import sitemap from '@astrojs/sitemap';
import { printSummary } from './src/utils/logger.ts';

// Deploy target (vercel | netlify | cloudflare | node). Default: vercel.
const deployTarget = import.meta.env.DEPLOY_TARGET || 'vercel';

// The site is SSG by default; pages that need an adapter opt in via
// `export const prerender = false`. `OUTPUT` can override (e.g. `server`).
const output = import.meta.env.OUTPUT || 'static';

// The FQDN of where the site is hosted (used for sitemaps & canonical URLs)
const site = import.meta.env.SITE_URL || 'https://awesome-privacy.xyz';

// Only import the adapter we actually need — keeps optional peer deps
// (e.g. cloudflare → wrangler) out of the install on other targets.
const loadAdapter = async () => {
	switch (deployTarget) {
		case 'vercel':
			return (await import('@astrojs/vercel')).default();
		case 'netlify':
			return (await import('@astrojs/netlify')).default();
		case 'cloudflare':
			return (await import('@astrojs/cloudflare')).default();
		case 'node':
			return (await import('@astrojs/node')).default({ mode: 'standalone' });
		default:
			return undefined;
	}
};

const buildLogger = {
	name: 'build-logger',
	hooks: {
		'astro:build:done': () => printSummary(),
	},
};

export default defineConfig({
	output,
	site,
	adapter: await loadAdapter(),
	integrations: [svelte(), partytown(), sitemap(), buildLogger],
	vite: {
		css: {
			preprocessorOptions: {
				scss: { api: 'modern' },
			},
		},
	},
});
