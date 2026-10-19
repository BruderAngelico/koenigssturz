import { PageHeader } from "@/components/layout/page-header"
import { CopyBlocks } from "@/components/sections/copy-blocks"
import { whoPage } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(whoPage.title, whoPage.description)

export default function WhoPage() {
  return (
    <>
      <PageHeader eyebrow={whoPage.eyebrow} title={whoPage.title} lede={whoPage.lede} />
      <CopyBlocks blocks={whoPage.blocks} />
    </>
  )
}
