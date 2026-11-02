import * as React from "react"
import { cn } from "@/lib/utils"

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "has-data" | "no-data"
}

function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
        variant === "has-data" && "bg-emerald-50 text-emerald-700",
        variant === "no-data" && "text-slate-400",
        variant === "default" && "bg-slate-100 text-slate-600",
        className
      )}
      {...props}
    />
  )
}

export { Badge }
