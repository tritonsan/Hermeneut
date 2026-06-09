export function ConfidenceBar({value}: {value: number}) {
  const percent = Math.round(value * 100);
  return (
    <div className="w-full">
      <div className="mb-1 flex items-center justify-between text-xs text-ink/65">
        <span>Confidence</span>
        <span>{percent}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-line">
        <div className="h-full rounded-full bg-copper" style={{width: `${Math.max(4, percent)}%`}} />
      </div>
    </div>
  );
}
