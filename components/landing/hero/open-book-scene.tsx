'use client'

import { Environment, Lightformer, PerformanceMonitor, Sparkles } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { LuxTheme } from '../lux/use-lux-theme'
import { makeGlow, makeGlyphs, makeLeather, makeLinen, makePageTexture, SCENE_PALETTE } from './scene-textures'

const PAGE_W = 1.5
const PAGE_H = 2.05
const SEGMENTS = 40
const STACK = 7
const FLIP_CYCLE = 8
const FLIP_DURATION = 2.6

/** Height of a resting page above the board, t = 0 at the spine, 1 at the fore-edge. */
function restLift(t: number) {
  return 0.22 * Math.sin(Math.PI * Math.min(1, t * 1.05)) * (1 - 0.55 * t) + 0.02 * t
}
function restSlope(t: number) {
  const dt = 1 / SEGMENTS
  return Math.atan2(restLift(Math.min(1, t + dt)) - restLift(t), PAGE_W * dt)
}
const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2)
const smooth = (a: number, b: number, x: number) => {
  const t = THREE.MathUtils.clamp((x - a) / (b - a), 0, 1)
  return t * t * (3 - 2 * t)
}

function makeRestingPage(side: 1 | -1, liftScale: number) {
  const geometry = new THREE.PlaneGeometry(PAGE_W, PAGE_H, SEGMENTS, 1)
  const pos = geometry.attributes.position
  for (let i = 0; i < pos.count; i++) {
    const t = (pos.getX(i) + PAGE_W / 2) / PAGE_W
    pos.setX(i, side * t * PAGE_W)
    pos.setZ(i, restLift(t) * liftScale)
  }
  if (side === -1) {
    const uv = geometry.attributes.uv
    for (let i = 0; i < uv.count; i++) uv.setX(i, 1 - uv.getX(i))
    if (geometry.index) (geometry.index.array as Uint16Array).reverse()
  }
  geometry.computeVertexNormals()
  return geometry
}

export interface SceneDrivers {
  /** 0..1 scroll progress through the hero, written by GSAP ScrollTrigger. */
  scroll: number
  /** -1..1 pointer position (mouse only). */
  px: number
  py: number
}

/** A page whose curl is scrubbed by scroll, or idles through a slow loop at the top. */
function TurningPage({ front, back, drivers, animate }: { front: THREE.Texture | null; back: THREE.Texture | null; drivers: React.RefObject<SceneDrivers>; animate: boolean }) {
  const geometry = useMemo(() => new THREE.PlaneGeometry(PAGE_W, PAGE_H, SEGMENTS, 6), [])
  const slopes = useMemo(() => Array.from({ length: SEGMENTS + 1 }, (_, i) => restSlope(i / SEGMENTS)), [])
  const pts = useMemo(() => new Float32Array((SEGMENTS + 1) * 2), [])
  const current = useRef(0)

  useEffect(() => () => geometry.dispose(), [geometry])

  useFrame((state, delta) => {
    const s = drivers.current.scroll
    const time = state.clock.elapsedTime % (FLIP_CYCLE * 2)
    const half = time % FLIP_CYCLE
    const forward = time < FLIP_CYCLE
    const raw = Math.min(1, Math.max(0, (half - (FLIP_CYCLE - FLIP_DURATION - 1)) / FLIP_DURATION))
    const idle = animate ? (forward ? easeInOut(raw) : 1 - easeInOut(raw)) : 0
    const scrolled = smooth(0.12, 0.62, s)
    const target = s > 0.03 ? scrolled : idle
    current.current = THREE.MathUtils.damp(current.current, target, s > 0.03 ? 9 : 30, delta)
    const progress = current.current
    const theta = progress * Math.PI
    const curl = 1.05 * Math.sin(theta)

    const seg = PAGE_W / SEGMENTS
    let x = 0
    let z = restLift(0) + 0.004
    pts[0] = x
    pts[1] = z
    for (let i = 1; i <= SEGMENTS; i++) {
      const t = i / SEGMENTS
      const alpha = slopes[i - 1]
      const phi = alpha + (Math.PI - 2 * alpha) * progress - curl * t
      x += Math.cos(phi) * seg
      z += Math.sin(phi) * seg
      pts[i * 2] = x
      pts[i * 2 + 1] = z + 0.006 * Math.sin(theta)
    }
    const pos = geometry.attributes.position
    const cols = SEGMENTS + 1
    for (let v = 0; v < pos.count; v++) {
      const i = v % cols
      const row = Math.floor(v / cols) / 6 // 0 top → 1 bottom
      // Diagonal lag: the lower corner leads the turn, like a thumb lifting it.
      const lag = Math.sin(theta) * 0.08 * (row - 0.5) * (i / SEGMENTS)
      pos.setX(v, pts[i * 2] * Math.cos(lag))
      pos.setZ(v, pts[i * 2 + 1] + Math.abs(lag) * 0.6)
    }
    pos.needsUpdate = true
    geometry.computeVertexNormals()
  })

  return (
    <group>
      <mesh geometry={geometry} castShadow>
        <meshStandardMaterial map={front ?? undefined} roughness={0.88} side={THREE.FrontSide} />
      </mesh>
      <mesh geometry={geometry}>
        <meshStandardMaterial map={back ?? undefined} roughness={0.88} side={THREE.BackSide} />
      </mesh>
    </group>
  )
}

function RisingGlyphs({ animate, count, dark }: { animate: boolean; count: number; dark: boolean }) {
  const textures = useMemo(() => makeGlyphs(), [])
  const seeds = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => ({
        tex: i % Math.max(1, textures.length),
        x: (((i * 0.6180339) % 1) - 0.5) * 2.6,
        z: ((((i * 0.414) % 1) - 0.5) * 1.6),
        phase: (i * 0.137) % 1,
        speed: 0.05 + ((i * 0.29) % 1) * 0.05,
        sway: 0.3 + ((i * 0.53) % 1) * 0.6,
        scale: 0.32 + ((i * 0.71) % 1) * 0.22,
      })),
    [count, textures.length],
  )
  const refs = useRef<(THREE.Sprite | null)[]>([])

  useEffect(() => () => textures.forEach((t) => t.dispose()), [textures])

  useFrame((state) => {
    const t = state.clock.elapsedTime
    seeds.forEach((s, i) => {
      const sprite = refs.current[i]
      if (!sprite) return
      const f = animate ? (t * s.speed + s.phase) % 1 : s.phase
      sprite.position.set(s.x + Math.sin(t * s.sway + i) * 0.12 * f, 0.25 + f * 2.4, s.z)
      const mat = sprite.material as THREE.SpriteMaterial
      mat.opacity = Math.sin(Math.PI * f) * 0.85
      const k = s.scale * (0.7 + f * 0.5)
      sprite.scale.set(k * 2, k, 1)
    })
  })

  if (!textures.length) return null
  return (
    <group>
      {seeds.map((s, i) => (
        <sprite
          key={i}
          ref={(el) => {
            refs.current[i] = el
          }}
        >
          <spriteMaterial
            map={textures[s.tex]}
            transparent
            depthWrite={false}
            opacity={0}
            color={dark ? '#ffffff' : '#7c5a1e'}
            blending={dark ? THREE.AdditiveBlending : THREE.NormalBlending}
          />
        </sprite>
      ))}
    </group>
  )
}

const coneVertex = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vUv = uv;
    vec4 world = modelMatrix * vec4(position, 1.0);
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vViewDir = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`
const coneFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    float edge = pow(abs(dot(vNormalW, vViewDir)), 2.2);
    float fall = smoothstep(0.0, 0.85, vUv.y);
    gl_FragColor = vec4(uColor, uOpacity * edge * fall);
  }
`

/** Fake volumetric shaft from the reading lamp. */
function LightShaft({ opacity }: { opacity: number }) {
  const uniforms = useMemo(() => ({ uColor: { value: new THREE.Color('#ffd79a') }, uOpacity: { value: opacity } }), [opacity])
  return (
    <mesh position={[0.35, 2.4, 0.15]} rotation={[0, 0, -0.08]}>
      <coneGeometry args={[2.1, 4.8, 64, 1, true]} />
      <shaderMaterial
        vertexShader={coneVertex}
        fragmentShader={coneFragment}
        uniforms={uniforms}
        transparent
        depthWrite={false}
        side={THREE.DoubleSide}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  )
}

function Book({ drivers, animate, shadows }: { drivers: React.RefObject<SceneDrivers>; animate: boolean; shadows: boolean }) {
  const group = useRef<THREE.Group>(null)
  const ribbon = useRef<THREE.Mesh>(null)
  const tex = useMemo(
    () => ({
      left: makePageTexture('chapter'),
      under: makePageTexture('recap'),
      front: makePageTexture('diagram'),
      back: makePageTexture('code', true),
      leather: makeLeather(),
      linen: makeLinen(),
    }),
    [],
  )
  const stacks = useMemo(
    () =>
      ([1, -1] as const).flatMap((side) =>
        Array.from({ length: STACK }, (_, i) => ({ geometry: makeRestingPage(side, 1 - i * 0.06), z: -i * 0.014, key: `${side}-${i}`, side, top: i === 0 })),
      ),
    [],
  )
  const gilt = useMemo(() => new THREE.MeshStandardMaterial({ color: SCENE_PALETTE.gilt, metalness: 1, roughness: 0.3 }), [])
  const paperEdge = useMemo(() => new THREE.MeshStandardMaterial({ color: SCENE_PALETTE.paperShade, roughness: 0.95, map: tex.linen ?? undefined }), [tex.linen])
  // Box faces: +x, -x, +y, -y, +z, -z. Fore-edge and head/tail are gilt.
  const blockMaterials = useMemo(
    () => ({
      right: [gilt, paperEdge, gilt, gilt, paperEdge, paperEdge],
      left: [paperEdge, gilt, gilt, gilt, paperEdge, paperEdge],
    }),
    [gilt, paperEdge],
  )

  useEffect(
    () => () => {
      tex.left?.dispose()
      tex.under?.dispose()
      tex.front?.dispose()
      tex.back?.dispose()
      tex.leather.map?.dispose()
      tex.leather.bump?.dispose()
      tex.linen?.dispose()
      gilt.dispose()
      paperEdge.dispose()
      stacks.forEach((p) => p.geometry.dispose())
    },
    [tex, stacks, gilt, paperEdge],
  )

  useFrame((state) => {
    const t = state.clock.elapsedTime
    if (group.current && animate) group.current.position.y = Math.sin(t * 0.7) * 0.035
    if (ribbon.current && animate) ribbon.current.rotation.y = Math.sin(t * 1.1) * 0.06
  })

  return (
    <group ref={group}>
      <group rotation={[-Math.PI / 2, 0, 0.12]}>
        {/* Leather boards */}
        {([1, -1] as const).map((side) => (
          <mesh key={side} position={[side * (PAGE_W / 2 + 0.04), 0, -0.135]} rotation={[0, side * -0.035, 0]} castShadow={shadows} receiveShadow={shadows}>
            <boxGeometry args={[PAGE_W + 0.12, PAGE_H + 0.16, 0.055]} />
            <meshStandardMaterial
              color="#ffffff"
              map={tex.leather.map ?? undefined}
              bumpMap={tex.leather.bump ?? undefined}
              bumpScale={0.6}
              roughness={0.62}
              metalness={0.05}
            />
          </mesh>
        ))}
        {/* Gilt fillet around the boards */}
        {([1, -1] as const).map((side) => (
          <mesh key={`fillet-${side}`} position={[side * (PAGE_W / 2 + 0.04), 0, -0.106]} material={gilt}>
            <boxGeometry args={[PAGE_W + 0.13, PAGE_H + 0.17, 0.004]} />
          </mesh>
        ))}
        {/* Rounded spine */}
        <mesh position={[0, 0, -0.15]} rotation={[0, 0, 0]}>
          <cylinderGeometry args={[0.085, 0.085, PAGE_H + 0.16, 32, 1, true, Math.PI / 2, Math.PI]} />
          <meshStandardMaterial color={SCENE_PALETTE.leatherDeep} roughness={0.55} side={THREE.DoubleSide} />
        </mesh>
        {/* Headbands */}
        {([1, -1] as const).map((end) => (
          <mesh key={`hb-${end}`} position={[0, end * (PAGE_H / 2 + 0.005), -0.07]} rotation={[0, 0, Math.PI / 2]} material={gilt}>
            <cylinderGeometry args={[0.02, 0.02, 0.12, 12]} />
          </mesh>
        ))}
        {/* Gilt page blocks */}
        <mesh position={[PAGE_W / 2, 0, -0.06]} material={blockMaterials.right} receiveShadow={shadows}>
          <boxGeometry args={[PAGE_W - 0.02, PAGE_H - 0.02, 0.1]} />
        </mesh>
        <mesh position={[-PAGE_W / 2, 0, -0.06]} material={blockMaterials.left} receiveShadow={shadows}>
          <boxGeometry args={[PAGE_W - 0.02, PAGE_H - 0.02, 0.1]} />
        </mesh>

        {stacks.map((page) => (
          <mesh key={page.key} geometry={page.geometry} position={[0, 0, page.z]} receiveShadow={shadows && page.top}>
            <meshStandardMaterial
              map={page.top ? ((page.side === 1 ? tex.under : tex.left) ?? undefined) : undefined}
              color={page.top ? '#ffffff' : SCENE_PALETTE.paper}
              roughness={0.9}
            />
          </mesh>
        ))}

        <TurningPage front={tex.front} back={tex.back} drivers={drivers} animate={animate} />

        {/* Silk ribbon marker */}
        <mesh ref={ribbon} position={[0.08, -PAGE_H / 2 - 0.28, 0.05]} rotation={[0.1, 0, 0.05]}>
          <planeGeometry args={[0.07, 0.62]} />
          <meshStandardMaterial color={SCENE_PALETTE.ribbon} roughness={0.35} metalness={0.15} side={THREE.DoubleSide} />
        </mesh>
      </group>
    </group>
  )
}

function CameraRig({ drivers, animate }: { drivers: React.RefObject<SceneDrivers>; animate: boolean }) {
  const { camera, size } = useThree()
  const look = useMemo(() => new THREE.Vector3(), [])
  const from = useMemo(() => new THREE.Vector3(0, 2.5, 5.2), [])
  const to = useMemo(() => new THREE.Vector3(0.5, 4.6, 1.45), [])
  const tmp = useMemo(() => new THREE.Vector3(), [])

  useFrame((_, delta) => {
    const d = drivers.current
    const p = smooth(0, 1, d.scroll)
    // Narrow viewports pull back so the spread always fits.
    const aspect = size.width / Math.max(1, size.height)
    const fit = aspect < 1.1 ? THREE.MathUtils.clamp(1.25 / aspect, 1, 2.3) : 1
    tmp.lerpVectors(from, to, p).multiplyScalar(fit)
    if (animate) {
      tmp.x += d.px * 0.35
      tmp.y += -d.py * 0.2
    }
    camera.position.x = THREE.MathUtils.damp(camera.position.x, tmp.x, 4, delta)
    camera.position.y = THREE.MathUtils.damp(camera.position.y, tmp.y, 4, delta)
    camera.position.z = THREE.MathUtils.damp(camera.position.z, tmp.z, 4, delta)
    look.set(0, 0.05 - p * 0.15, p * 0.15)
    camera.lookAt(look)
  })
  return null
}

function Shadow() {
  const tex = useMemo(() => makeGlow('rgba(0,0,0,0.65)', 'rgba(0,0,0,0.25)'), [])
  useEffect(() => () => tex?.dispose(), [tex])
  return (
    <mesh position={[0, -0.2, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[5.2, 3.8]} />
      <meshBasicMaterial map={tex ?? undefined} transparent depthWrite={false} opacity={0.75} />
    </mesh>
  )
}

export default function OpenBookScene({
  active,
  animate,
  drivers,
  theme,
  rich,
}: {
  active: boolean
  animate: boolean
  drivers: React.RefObject<SceneDrivers>
  theme: LuxTheme
  /** Desktop-class device: shadows, more particles, env reflections. */
  rich: boolean
}) {
  const [dpr, setDpr] = useState(rich ? 1.6 : 1.25)
  const [degraded, setDegraded] = useState(false)
  const dark = theme === 'dark'
  const shadows = rich && !degraded

  return (
    <Canvas
      dpr={dpr}
      shadows={shadows}
      frameloop={active ? 'always' : 'never'}
      camera={{ position: [0, 2.5, 5.2], fov: 34, near: 0.1, far: 40 }}
      gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.ACESFilmicToneMapping
        gl.toneMappingExposure = 1.05
      }}
      aria-hidden="true"
    >
      <PerformanceMonitor
        onDecline={() => {
          setDpr(1)
          setDegraded(true)
        }}
        onIncline={() => setDpr(rich ? 1.6 : 1.25)}
      />
      <CameraRig drivers={drivers} animate={animate} />
      <ambientLight intensity={dark ? 0.16 : 0.5} color="#fff1dc" />
      <hemisphereLight args={['#ffe7c2', '#2a1a10', dark ? 0.35 : 0.6]} />
      <spotLight
        position={[0.6, 5, 1.1]}
        angle={0.5}
        penumbra={0.85}
        intensity={dark ? 70 : 48}
        color="#ffd9a0"
        castShadow={shadows}
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-bias={-0.0004}
      />
      <pointLight position={[-3, 1.4, -1.5]} intensity={dark ? 9 : 4} color="#d4ae66" />
      <pointLight position={[2.8, 0.8, 2.4]} intensity={dark ? 4 : 2} color="#ffb98a" />

      {rich && !degraded && (
        <Environment resolution={64} frames={1}>
          <Lightformer intensity={2.2} color="#ffe2b0" position={[0, 5, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[6, 6, 1]} />
          <Lightformer intensity={1.2} color="#d4ae66" position={[-4, 1, 2]} rotation={[0, Math.PI / 2, 0]} scale={[3, 2, 1]} />
          <Lightformer intensity={0.8} color="#ffffff" position={[4, 2, -2]} rotation={[0, -Math.PI / 2, 0]} scale={[3, 2, 1]} />
        </Environment>
      )}

      <Shadow />
      <Book drivers={drivers} animate={animate} shadows={shadows} />
      <LightShaft opacity={dark ? 0.13 : 0.07} />
      <RisingGlyphs animate={animate} count={rich ? 22 : 12} dark={dark} />
      <Sparkles count={rich ? 80 : 40} scale={[5, 3.5, 3]} position={[0, 1.4, 0]} size={2.2} speed={animate ? 0.25 : 0} opacity={dark ? 0.8 : 0.55} color={dark ? '#f0d595' : '#b8893b'} noise={0.7} />
    </Canvas>
  )
}
