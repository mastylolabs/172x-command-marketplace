export interface StarterWidgetProps { readonly label: string }

export function StarterWidget({ label }: StarterWidgetProps) {
  return { kind: "host-bundled-review-source", label } as const
}
