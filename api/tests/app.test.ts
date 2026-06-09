// Integration tests against the assembled app (no upstream calls)
import { describe, it, expect } from 'vitest'
import { buildApp } from '../src/app'

const app = buildApp()
const env = { API_TOKEN: 'test' }

const hit = (path: string, init?: RequestInit) =>
  app.fetch(new Request(`http://localhost${path}`, init), env)

describe('public routes', () => {
  it('health returns ok envelope', async () => {
    const res = await hit('/v1/health')
    expect(res.status).toBe(200)
    expect(await res.json()).toMatchObject({ data: { status: 'ok' } })
  })

  it('stats has positive counts', async () => {
    const res = await hit('/v1/stats')
    const { data } = (await res.json()) as any
    expect(data.categories).toBeGreaterThan(0)
    expect(data.services).toBeGreaterThan(0)
  })

  it('categories returns wrapped list', async () => {
    const res = await hit('/v1/categories')
    const body = (await res.json()) as any
    expect(Array.isArray(body.data)).toBe(true)
    expect(body.data[0]).toHaveProperty('slug')
  })

  it('unknown service returns NOT_FOUND envelope', async () => {
    const res = await hit('/v1/services/nope-nope')
    expect(res.status).toBe(404)
    expect(await res.json()).toMatchObject({ error: { code: 'NOT_FOUND' } })
  })

  it('search without q returns BAD_REQUEST envelope', async () => {
    const res = await hit('/v1/search')
    expect(res.status).toBe(400)
    expect(await res.json()).toMatchObject({ error: { code: 'BAD_REQUEST' } })
  })

  it('search returns results for known term', async () => {
    const res = await hit('/v1/search?q=password&limit=3')
    const body = (await res.json()) as any
    expect(body.data.length).toBeGreaterThan(0)
  })

  it('services paginates', async () => {
    const res = await hit('/v1/services?limit=5')
    const body = (await res.json()) as any
    expect(body.data).toHaveLength(5)
    expect(body.pagination).toMatchObject({ page: 1, limit: 5, hasMore: true })
  })
})

describe('auth', () => {
  it('private route rejects missing token', async () => {
    const res = await hit('/v1/enrich/privacy/1')
    expect(res.status).toBe(401)
  })

  it('private route rejects wrong token', async () => {
    const res = await hit('/v1/enrich/privacy/1', {
      headers: { authorization: 'Bearer wrong' },
    })
    expect(res.status).toBe(401)
  })

  it('security enrich route requires auth', async () => {
    const res = await hit('/v1/enrich/security/jquery/jquery')
    expect(res.status).toBe(401)
  })

  it('docker enrich route requires auth', async () => {
    const res = await hit('/v1/enrich/docker/pihole')
    expect(res.status).toBe(401)
  })
})

describe('middleware scoping', () => {
  it('public routes are edge cacheable', async () => {
    const res = await hit('/v1/stats')
    expect(res.headers.get('cache-control')).toBe('public, s-maxage=300')
  })

  it('enrich routes are not advertised as public cacheable', async () => {
    const res = await hit('/v1/enrich/privacy/1')
    expect(res.headers.get('cache-control') ?? '').not.toContain('public')
  })
})

describe('mcp', () => {
  it('lists tools', async () => {
    const res = await hit('/v1/mcp', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    })
    const body = (await res.json()) as any
    expect(body.result.tools.length).toBeGreaterThan(0)
  })

  it('runs get_service tool', async () => {
    const res = await hit('/v1/mcp', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: { name: 'get_service', arguments: { slug: 'bitwarden' } },
      }),
    })
    const body = (await res.json()) as any
    const out = JSON.parse(body.result.content[0].text)
    expect(out.name).toBe('Bitwarden')
  })
})
