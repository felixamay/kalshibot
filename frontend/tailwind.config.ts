import type { Config } from "tailwindcss";

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07110d",
          900: "#0c1a14",
          800: "#13261e",
          700: "#1a3329",
          600: "#244537",
        },
        signal: {
          lime: "#b8f000",
          amber: "#ffb020",
          coral: "#ff5c4d",
          mint: "#3dd6c6",
        },
        mist: "#d7e4dc",
      },
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      animation: {
        "pulse-signal": "pulse-signal 1.2s ease-in-out infinite",
        "bar-shrink": "none",
      },
      keyframes: {
        "pulse-signal": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.72" },
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
