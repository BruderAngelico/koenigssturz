const nodes = [
  { n: "01", title: "Vollversammlung" },
  { n: "02", title: "Arbeitsgruppen" },
  { n: "03", title: "Delegierte" },
  { n: "04", title: "Rechenschaft / Abwahl" },
]

/** Decorative diagram. The same steps are in the text beside it. */
export function CouncilDiagram() {
  return (
    <div className="border-2 border-line bg-surface p-4 shadow-hard">
      <svg viewBox="0 0 640 220" className="h-auto w-full">
        <rect width="640" height="220" fill="#F4EFE6" />
        <path d="M40 150 H600" stroke="#111111" strokeWidth="6" />
        <path d="M560 110 V170 H70" fill="none" stroke="#C8102E" strokeWidth="6" />
        <polygon points="70,170 86,160 86,180" fill="#C8102E" />
        {nodes.map((node, index) => {
          const x = 36 + index * 150
          return (
            <g key={node.n} transform={`translate(${x} 28)`}>
              <rect width="130" height="78" fill={index === 3 ? "#1F4D2B" : "#111111"} />
              <text x="10" y="28" fill="#F2C300" fontSize="16" fontFamily="sans-serif">
                {node.n}
              </text>
              <text x="10" y="54" fill="#F4EFE6" fontSize="13" fontFamily="sans-serif">
                {node.title.split(" ")[0]}
              </text>
            </g>
          )
        })}
      </svg>
      <p className="mt-2 text-sm font-semibold text-ink">
        Roter Rückweg: das Mandat kehrt in die Versammlung zurück.
      </p>
    </div>
  )
}
