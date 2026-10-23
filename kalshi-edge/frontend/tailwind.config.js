/** Tailwind build config. Rebuild after editing class names: see README "Editing the dashboard". */
module.exports = {
  content: ['./static/index.html', './static/app.js'],
  theme: {
    extend: {
      colors: {
        ink: {
          100: '#ecedef', 200: '#d5d8dc', 300: '#b4b9c0', 400: '#8b919a', 500: '#5b6069', 600: '#3d4148',
          700: '#2c2f34', 800: '#1f2125', 850: '#181a1d', 900: '#141518', 950: '#0d0e10',
        },
      },
    },
  },
};
