import type { MetadataRoute } from 'next'

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Bookey: your narrated learning book',
    short_name: 'Bookey',
    description: 'Interactive narrated books with a voice tutor, diagrams and a Python lab. Works offline once saved.',
    start_url: '/dashboard',
    scope: '/',
    display: 'standalone',
    background_color: '#1a0f12',
    theme_color: '#c2410c',
    icons: [
      { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml' },
      { src: '/apple-icon.png', sizes: '180x180', type: 'image/png' },
    ],
  }
}
