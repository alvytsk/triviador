import * as SelectPrimitive from "@radix-ui/react-select";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "../lib";

/** Vendored from shadcn/ui on top of `@radix-ui/react-select`, restyled to
 *  the dark broadcast palette. Re-exported as the plain Radix names
 *  (`Select`, `SelectTrigger`, …) rather than one combined component: a
 *  filter bar composes these directly (`<Select><SelectTrigger>…`), same
 *  as upstream shadcn usage, so callers don't need a second API to learn. */
export const Select = SelectPrimitive.Root;
export const SelectGroup = SelectPrimitive.Group;
export const SelectValue = SelectPrimitive.Value;

const triggerVariants = cva(
  cn(
    "flex w-full items-center justify-between gap-2 bg-raised border-2 border-line px-4",
    "text-[14px] font-medium text-ink outline-none transition-colors",
    "focus:border-gold data-[placeholder]:text-ink-faint",
    "disabled:cursor-not-allowed disabled:opacity-60",
  ),
  {
    variants: {
      size: { default: "h-11", sm: "h-9" },
    },
    defaultVariants: { size: "default" },
  },
);

export function SelectTrigger({
  className,
  children,
  size = "default",
  ...props
}: ComponentProps<typeof SelectPrimitive.Trigger> & VariantProps<typeof triggerVariants>) {
  return (
    <SelectPrimitive.Trigger className={cn(triggerVariants({ size }), className)} {...props}>
      {children}
      <SelectPrimitive.Icon asChild>
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-4 w-4 shrink-0 text-ink-dim"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path d="m5 8 5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({
  className,
  children,
  position = "popper",
  ...props
}: ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        className={cn(
          "z-50 max-h-96 min-w-[var(--radix-select-trigger-width)] overflow-hidden",
          "border-2 border-line bg-panel text-ink shadow-lg",
          position === "popper" &&
            "data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1",
          className,
        )}
        position={position}
        {...props}
      >
        <SelectPrimitive.Viewport
          className={cn(
            "p-1",
            position === "popper" &&
              "h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)]",
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "relative flex cursor-pointer select-none items-center px-3 py-2 text-[13px] font-medium",
        "outline-none data-[highlighted]:bg-raised data-[state=checked]:text-gold",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}
