import { PageHeader } from "@/components/layout/page-header"
import { CouncilDiagram } from "@/components/motifs/council-diagram"
import { CopyBlocks } from "@/components/sections/copy-blocks"
import { councilPage } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(councilPage.title, councilPage.description)

export default function CouncilPage() {
  return (
    <>
      <PageHeader eyebrow={councilPage.eyebrow} title={councilPage.title} lede={councilPage.lede} />
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:px-6 lg:grid-cols-2">
        <div>
          <h2 className="font-display text-3xl uppercase leading-none">{councilPage.cycleLabel}</h2>
          <p className="mt-4 leading-relaxed">{councilPage.cycleNote}</p>
        </div>
        <CouncilDiagram />
      </div>
      <CopyBlocks blocks={councilPage.blocks} />
    </>
  )
}
