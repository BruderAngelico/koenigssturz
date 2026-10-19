import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "lift inline-flex items-center justify-center gap-2 border-2 border-line px-4 text-center text-sm font-semibold uppercase tracking-wide transition-transform disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary: "bg-resistance text-paper shadow-hard",
        outline: "bg-transparent text-ink shadow-hard-sm hover:bg-ink hover:text-background",
        inverse: "border-charcoal bg-paper text-charcoal shadow-hard",
        ghost: "border-transparent bg-transparent text-ink shadow-none",
      },
      size: {
        sm: "h-10 px-3",
        md: "h-11 px-4",
        lg: "h-12 px-5 text-base",
        icon: "size-11 px-0",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
)

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }

function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  if (asChild) {
    return <Slot className={cn(buttonVariants({ variant, size }), className)} {...props} />
  }

  return (
    <button
      type={type ?? "button"}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { Button, buttonVariants }
