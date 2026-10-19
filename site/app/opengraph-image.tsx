import { ImageResponse } from "next/og"

export const alt = "Rätegrün — Gemeinsam anbauen. Gemeinsam entscheiden."
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#F4EFE6",
          color: "#111111",
          padding: 64,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <div style={{ display: "flex", background: "#C8102E", color: "#F4EFE6", fontSize: 32, padding: "8px 16px" }}>
            RÄTEGRÜN
          </div>
          <div style={{ display: "flex", background: "#F2C300", color: "#111111", fontSize: 32, padding: "8px 16px" }}>
            18+
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", fontSize: 76, lineHeight: 0.9, fontWeight: 700 }}>
          <div style={{ display: "flex" }}>GEMEINSAM ANBAUEN.</div>
          <div style={{ display: "flex", background: "#1F4D2B", color: "#F4EFE6", padding: "4px 12px" }}>
            GEMEINSAM ENTSCHEIDEN.
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 28 }}>Solidarisch. Basisdemokratisch. Ohne Profit.</div>
      </div>
    ),
    { ...size },
  )
}
