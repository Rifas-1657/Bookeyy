'use client'

import { AnimatePresence, motion } from 'framer-motion'
import { Plus } from 'lucide-react'
import { useId, useState } from 'react'
import { easeOutExpo } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { FAQS } from './data'
import { SectionHeading } from './lux/section-heading'

export function Faq() {
  const [open, setOpen] = useState<number | null>(0)
  const baseId = useId()

  return (
    <section aria-labelledby="faq-title" className="bg-lux-bg-2 py-24 sm:py-32">
      <div className="mx-auto grid max-w-7xl gap-12 px-4 sm:px-8 lg:grid-cols-[1fr_1.5fr]">
        <SectionHeading
          id="faq-title"
          eyebrow="Questions"
          title={
            <>
              Asked, <em className="italic text-lux-gold-text">answered.</em>
            </>
          }
        />
        <ul className="divide-y divide-lux-line border-y border-lux-line">
          {FAQS.map((item, i) => {
            const isOpen = open === i
            const buttonId = `${baseId}-q-${i}`
            const panelId = `${baseId}-a-${i}`
            return (
              <li key={item.q}>
                <h3>
                  <button
                    id={buttonId}
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={panelId}
                    onClick={() => setOpen(isOpen ? null : i)}
                    className="flex w-full items-center justify-between gap-6 py-6 text-left font-serif text-lg font-normal text-lux-fg focus-visible:text-lux-gold-text focus-visible:outline-none sm:text-xl"
                  >
                    {item.q}
                    <Plus aria-hidden="true" className={cn('size-5 shrink-0 text-lux-gold-text transition-transform duration-300', isOpen && 'rotate-45')} />
                  </button>
                </h3>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      id={panelId}
                      role="region"
                      aria-labelledby={buttonId}
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.4, ease: easeOutExpo }}
                      className="overflow-hidden"
                    >
                      <p className="max-w-2xl pb-6 leading-relaxed text-lux-muted">{item.a}</p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
