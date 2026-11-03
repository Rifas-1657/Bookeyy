import * as THREE from 'three'

/**
 * Procedural canvas textures for the hero book. 3D materials can't read CSS
 * tokens, so the physical palette (leather, gilt, paper) lives here.
 */
export const SCENE_PALETTE = {
  leather: '#4a1a1c',
  leatherDeep: '#2a0e10',
  gilt: '#c9a057',
  giltBright: '#f0d595',
  paper: '#f7eedb',
  paperShade: '#eadbbd',
  ink: '#241c12',
  inkSoft: 'rgba(36,28,18,0.22)',
  accent: '#8a6322',
  ribbon: '#7a1f27',
} as const

function canvas(w: number, h: number) {
  const el = document.createElement('canvas')
  el.width = w
  el.height = h
  return { el, ctx: el.getContext('2d') }
}

function toTexture(el: HTMLCanvasElement, color = true) {
  const texture = new THREE.CanvasTexture(el)
  if (color) texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

/** Seeded PRNG so textures are identical every load. */
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

/** Pebbled leather grain: returns colour map and a matching bump map. */
export function makeLeather() {
  const size = 512
  const color = canvas(size, size)
  const bump = canvas(size, size)
  if (!color.ctx || !bump.ctx) return { map: null, bump: null }
  const r = rng(7)
  color.ctx.fillStyle = SCENE_PALETTE.leather
  color.ctx.fillRect(0, 0, size, size)
  bump.ctx.fillStyle = '#808080'
  bump.ctx.fillRect(0, 0, size, size)
  for (let i = 0; i < 5200; i++) {
    const x = r() * size
    const y = r() * size
    const rad = 1.5 + r() * 4.5
    const shade = r()
    color.ctx.fillStyle = shade > 0.5 ? `rgba(120,50,45,${0.05 + r() * 0.08})` : `rgba(10,3,3,${0.08 + r() * 0.12})`
    color.ctx.beginPath()
    color.ctx.arc(x, y, rad, 0, Math.PI * 2)
    color.ctx.fill()
    const g = Math.round(90 + shade * 110)
    bump.ctx.fillStyle = `rgba(${g},${g},${g},0.55)`
    bump.ctx.beginPath()
    bump.ctx.arc(x, y, rad, 0, Math.PI * 2)
    bump.ctx.fill()
  }
  // Worn edges catch more light.
  const wear = color.ctx.createRadialGradient(size / 2, size / 2, size * 0.25, size / 2, size / 2, size * 0.75)
  wear.addColorStop(0, 'rgba(0,0,0,0)')
  wear.addColorStop(1, 'rgba(160,90,70,0.18)')
  color.ctx.fillStyle = wear
  color.ctx.fillRect(0, 0, size, size)
  const map = toTexture(color.el)
  const bumpTex = toTexture(bump.el, false)
  for (const t of [map, bumpTex]) {
    t.wrapS = t.wrapT = THREE.RepeatWrapping
    t.repeat.set(2, 2)
  }
  return { map, bump: bumpTex }
}

/** Fine linen weave, used for the headband and page-block edges. */
export function makeLinen() {
  const size = 256
  const { el, ctx } = canvas(size, size)
  if (!ctx) return null
  ctx.fillStyle = SCENE_PALETTE.paperShade
  ctx.fillRect(0, 0, size, size)
  for (let y = 0; y < size; y += 2) {
    ctx.fillStyle = `rgba(90,70,40,${y % 4 === 0 ? 0.16 : 0.07})`
    ctx.fillRect(0, y, size, 1)
  }
  for (let x = 0; x < size; x += 3) {
    ctx.fillStyle = 'rgba(255,255,255,0.08)'
    ctx.fillRect(x, 0, 1, size)
  }
  const t = toTexture(el)
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  t.repeat.set(4, 1)
  return t
}

export type PageArt = 'chapter' | 'diagram' | 'code' | 'recap'

export function makePageTexture(art: PageArt, mirrored = false) {
  const W = 768
  const H = 1050
  const { el, ctx } = canvas(W, H)
  if (!ctx) return null
  if (mirrored) {
    ctx.translate(W, 0)
    ctx.scale(-1, 1)
  }
  const paper = ctx.createLinearGradient(0, 0, W, 0)
  paper.addColorStop(0, '#e9dcc0')
  paper.addColorStop(0.1, SCENE_PALETTE.paper)
  paper.addColorStop(1, '#fbf4e4')
  ctx.fillStyle = paper
  ctx.fillRect(0, 0, W, H)
  // Paper fibre.
  const r = rng(art.length * 31 + (mirrored ? 3 : 1))
  for (let i = 0; i < 900; i++) {
    ctx.fillStyle = `rgba(120,90,50,${r() * 0.05})`
    ctx.fillRect(r() * W, r() * H, 1 + r() * 2, 1)
  }

  const M = 96
  const line = (y: number, w: number, h = 9, color: string = SCENE_PALETTE.inkSoft) => {
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.roundRect(M, y, w, h, h / 2)
    ctx.fill()
  }
  const kicker = (text: string) => {
    ctx.fillStyle = SCENE_PALETTE.accent
    ctx.font = '600 22px ui-monospace, Menlo, monospace'
    ctx.fillText(text.split('').join(String.fromCharCode(8202)), M, 128)
    ctx.fillStyle = 'rgba(138,99,34,0.55)'
    ctx.fillRect(M, 148, 64, 2)
  }
  // Folio number.
  ctx.fillStyle = 'rgba(36,28,18,0.45)'
  ctx.font = 'italic 24px Georgia, serif'
  ctx.fillText(art === 'chapter' ? '17' : art === 'diagram' ? '18' : art === 'code' ? '19' : '20', W / 2 - 12, H - 64)

  if (art === 'chapter') {
    kicker('CHAPTER III')
    ctx.fillStyle = SCENE_PALETTE.ink
    ctx.font = '300 74px Georgia, serif'
    ctx.fillText('Loops that', M, 250)
    ctx.font = 'italic 300 74px Georgia, serif'
    ctx.fillText('repeat for you', M, 335)
    // Drop cap paragraph.
    ctx.font = '400 120px Georgia, serif'
    ctx.fillStyle = SCENE_PALETTE.accent
    ctx.fillText('I', M, 520)
    let y = 430
    for (let i = 0; i < 4; i++) {
      ctx.fillStyle = SCENE_PALETTE.inkSoft
      ctx.beginPath()
      ctx.roundRect(M + 70, y, i === 3 ? 330 : 480, 10, 5)
      ctx.fill()
      y += 34
    }
    for (let i = 0; i < 9; i++) {
      line(y, i % 4 === 3 ? 300 : 560, 10)
      y += 34
    }
    ctx.fillStyle = 'rgba(214,168,86,0.35)'
    ctx.fillRect(M - 6, 668, 380, 20)
    ctx.fillStyle = 'rgba(36,28,18,0.6)'
    ctx.font = 'italic 28px Georgia, serif'
    ctx.fillText('"Idhu dhaan loop — puriyudha?"', M, y + 40)
  }

  if (art === 'diagram') {
    kicker('SEE IT DRAWN')
    ctx.strokeStyle = SCENE_PALETTE.accent
    ctx.lineWidth = 5
    ctx.lineCap = 'round'
    const node = (x: number, y: number, w: number, label: string) => {
      ctx.beginPath()
      ctx.roundRect(x, y, w, 76, 38)
      ctx.stroke()
      ctx.fillStyle = SCENE_PALETTE.ink
      ctx.font = 'italic 30px Georgia, serif'
      ctx.fillText(label, x + 34, y + 48)
    }
    node(200, 220, 340, 'start')
    node(200, 400, 340, 'item in items?')
    node(200, 580, 340, 'print(item)')
    ctx.beginPath()
    ctx.moveTo(370, 296)
    ctx.lineTo(370, 400)
    ctx.moveTo(370, 476)
    ctx.lineTo(370, 580)
    ctx.moveTo(540, 618)
    ctx.bezierCurveTo(660, 618, 660, 438, 540, 438)
    ctx.stroke()
    ctx.fillStyle = SCENE_PALETTE.ribbon
    ctx.beginPath()
    ctx.arc(370, 348, 11, 0, Math.PI * 2)
    ctx.fill()
    let y = 760
    for (let i = 0; i < 5; i++) {
      line(y, i % 3 === 2 ? 320 : 560, 10)
      y += 34
    }
  }

  if (art === 'code') {
    kicker('TRY IT YOURSELF')
    ctx.fillStyle = '#211a12'
    ctx.beginPath()
    ctx.roundRect(M - 10, 200, W - 2 * M + 20, 300, 26)
    ctx.fill()
    ctx.font = '500 34px ui-monospace, Menlo, monospace'
    const row = (parts: [string, string][], y: number) => {
      let x = M + 30
      for (const [color, text] of parts) {
        ctx.fillStyle = color
        ctx.fillText(text, x, y)
        x += ctx.measureText(text).width
      }
    }
    row(
      [
        ['#e2c27f', 'for '],
        ['#f7eedb', 'item '],
        ['#e2c27f', 'in '],
        ['#d98a7a', 'items'],
        ['#f7eedb', ':'],
      ],
      280,
    )
    row([['#f7eedb', '    print(item)']], 340)
    row([['rgba(247,238,219,0.45)', '> mango  idli  dosa']], 440)
    let y = 580
    for (let i = 0; i < 8; i++) {
      line(y, i % 3 === 2 ? 300 : 560, 10)
      y += 34
    }
  }

  if (art === 'recap') {
    kicker('YOUR RECAP')
    let y = 230
    for (let i = 0; i < 4; i++) {
      ctx.strokeStyle = SCENE_PALETTE.accent
      ctx.lineWidth = 3
      ctx.beginPath()
      ctx.arc(M + 14, y - 4, 13, 0, Math.PI * 2)
      ctx.stroke()
      ctx.beginPath()
      ctx.moveTo(M + 7, y - 4)
      ctx.lineTo(M + 13, y + 3)
      ctx.lineTo(M + 22, y - 12)
      ctx.stroke()
      line(y - 10, 400, 10)
      y += 70
    }
    ctx.fillStyle = 'rgba(36,28,18,0.08)'
    ctx.beginPath()
    ctx.roundRect(M, 540, W - 2 * M, 260, 18)
    ctx.fill()
    ctx.strokeStyle = 'rgba(138,99,34,0.4)'
    ctx.setLineDash([10, 8])
    ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(36,28,18,0.5)'
    ctx.font = 'italic 26px Georgia, serif'
    ctx.fillText('screenshot · your loop output', M + 30, 690)
  }

  return toTexture(el)
}

/** Soft radial glow sprite. */
export function makeGlow(inner = 'rgba(255,214,150,0.95)', mid = 'rgba(214,168,86,0.3)') {
  const { el, ctx } = canvas(256, 256)
  if (!ctx) return null
  const g = ctx.createRadialGradient(128, 128, 0, 128, 128, 128)
  g.addColorStop(0, inner)
  g.addColorStop(0.4, mid)
  g.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, 256, 256)
  return toTexture(el)
}

const WORDS = ['loop', 'puriyudha?', 'f(x)', 'gradient', 'seri, next', 'recap', 'idea', '∑', 'super!', 'why?']

/** Word and diagram-stroke glyphs that rise off the pages. */
export function makeGlyphs() {
  const out: THREE.Texture[] = []
  for (let i = 0; i < WORDS.length + 4; i++) {
    const { el, ctx } = canvas(256, 128)
    if (!ctx) continue
    ctx.strokeStyle = SCENE_PALETTE.giltBright
    ctx.fillStyle = SCENE_PALETTE.giltBright
    ctx.lineWidth = 5
    ctx.lineCap = 'round'
    ctx.shadowColor = 'rgba(240,213,149,0.8)'
    ctx.shadowBlur = 12
    if (i < WORDS.length) {
      ctx.font = `italic ${WORDS[i].length > 6 ? 40 : 52}px Georgia, serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(WORDS[i], 128, 64)
    } else {
      const k = i - WORDS.length
      ctx.beginPath()
      if (k === 0) {
        ctx.moveTo(40, 90)
        ctx.bezierCurveTo(90, 10, 160, 120, 216, 40)
      } else if (k === 1) {
        ctx.arc(128, 64, 38, 0.3, Math.PI * 1.8)
      } else if (k === 2) {
        ctx.moveTo(50, 64)
        ctx.lineTo(200, 64)
        ctx.moveTo(176, 44)
        ctx.lineTo(204, 64)
        ctx.lineTo(176, 84)
      } else {
        ctx.roundRect(70, 34, 116, 60, 18)
      }
      ctx.stroke()
    }
    out.push(toTexture(el))
  }
  return out
}
