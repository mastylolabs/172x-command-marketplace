export type ClockWidgetProps = Readonly<{
  nowIso: string;
  locale: string;
  timeZone: string;
}>;

export function ClockWidget({ nowIso, locale, timeZone }: ClockWidgetProps) {
  const instant = new Date(nowIso);
  const display = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone,
  }).format(instant);

  return (
    <section aria-label="Clock">
      <time dateTime={nowIso}>{display}</time>
    </section>
  );
}
