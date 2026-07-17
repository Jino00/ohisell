import type { ButtonHTMLAttributes } from "react";

const VARIANT = {
  primary: "bg-blue-600 text-white hover:bg-blue-700",
  secondary: "bg-gray-100 text-gray-800 hover:bg-gray-200",
  ghost: "text-gray-600 hover:bg-gray-100",
} as const;

export function Button({ variant = "secondary", className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof VARIANT }) {
  return (
    <button
      className={`px-3 py-1.5 text-xs rounded border border-gray-200 disabled:opacity-50 ${VARIANT[variant]} ${className}`}
      {...rest}
    />
  );
}
