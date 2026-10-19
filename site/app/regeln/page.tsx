import Link from "next/link"
import { PageHeader } from "@/components/layout/page-header"
import { BulletBlock } from "@/components/sections/bullet-block"
import { rulesPage } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(rulesPage.title, rulesPage.description)

export default function RulesPage() {
  return (
    <>
      <PageHeader eyebrow={rulesPage.eyebrow} title={rulesPage.title} lede={rulesPage.lede} />
      <div className="mx-auto max-w-6xl space-y-12 px-4 py-14 sm:px-6">
        <BulletBlock title="Kodex" items={rulesPage.code} />
        <BulletBlock title="Awareness" items={rulesPage.awareness} />
        <BulletBlock title="Konflikte" items={rulesPage.conflict} />
        <BulletBlock title="Meldewege" items={rulesPage.reports} />
        <BulletBlock title="Konsequenzen" items={rulesPage.consequences} />
        <p>
          <Link href="/support" className="font-semibold underline decoration-2 underline-offset-4">
            Beratung und Support
          </Link>
        </p>
      </div>
    </>
  )
}
