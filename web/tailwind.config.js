/** Colours are CSS variables (RGB channels) so light and dark themes swap at runtime. */
const token = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        raised: token("raised"),
        sunken: token("sunken"),
        line: token("line"),
        ink: token("ink"),
        "ink-2": token("ink-2"),
        muted: token("muted"),
        primary: token("primary"),
        "primary-ink": token("primary-ink"),
        teal: token("teal"),
        amber: token("amber"),
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
        plum: token("plum"),
      },
      fontFamily: {
        display: ['"Space Grotesk"', "Inter", "system-ui", "sans-serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
        tamil: ['"Noto Sans Tamil"', "Nirmala UI", "sans-serif"],
      },
      borderRadius: { xl: "14px", "2xl": "18px", "3xl": "24px" },
      boxShadow: {
        glow: "0 0 0 1px rgb(var(--c-primary) / 0.45), 0 8px 30px -6px rgb(var(--c-primary) / 0.55)",
        card: "0 1px 0 0 rgb(255 255 255 / 0.04) inset, 0 10px 30px -12px rgb(0 0 0 / 0.45)",
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "fade-in": { from: { opacity: 0, transform: "translateY(4px)" }, to: { opacity: 1 } },
      },
      animation: {
        shimmer: "shimmer 1.6s infinite",
        "fade-in": "fade-in .25s ease-out both",
      },
    },
  },
  plugins: [],
};
