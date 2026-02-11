# Monthly Requests Chart Integration Guide

This document explains how to integrate the Monthly Requests chart component into your Django dashboard.

## Overview

The Monthly Requests chart displays a 12-month area chart with:
- Smooth gradient fill (indigo-500 to transparent)
- Monotone curve for organic look
- 1500ms animation on load with cubic-bezier easing
- Interactive tooltip with glassmorphism effect
- Active dot expansion on hover
- Crosshair vertical line following cursor
- Dark mode compatible
- Animated "Total Requests" counter

## Option 1: Chart.js Implementation (Recommended)

**File:** `static/js/monthly-requests-chart-chartjs.js`

This version uses Chart.js, which is already included in your project.

### Integration Steps

1. **Add to dashboard.html:**

   After the Recent Ads section, add:

   ```html
   {% include 'core/dashboard_monthly_chart.html' %}
   ```

   Or manually add:

   ```html
   <div class="dashboard-section mt-5">
     <div class="dashboard-chart-container" style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; padding: 1.5rem; box-shadow: var(--shadow-sm);">
       <div id="monthlyRequestsChart"></div>
     </div>
   </div>
   ```

2. **Add script to dashboard.html's `{% block extra_js %}`:**

   ```html
   <script src="{% static 'js/monthly-requests-chart-chartjs.js' %}"></script>
   <script>
   (function() {
     if (document.readyState === 'loading') {
       document.addEventListener('DOMContentLoaded', function() {
         if (window.MonthlyRequestsChartJS) {
           const darkMode = document.body.classList.contains('theme-dark');
           window.MonthlyRequestsChartJS.init('monthlyRequestsChart', { darkMode: darkMode });
         }
       });
     } else {
       if (window.MonthlyRequestsChartJS) {
         const darkMode = document.body.classList.contains('theme-dark');
         window.MonthlyRequestsChartJS.init('monthlyRequestsChart', { darkMode: darkMode });
       }
     }
   })();
   </script>
   ```

3. **Using Real Data (Optional):**

   Instead of mock data, pass real data from your Django view:

   ```python
   # In core/services/dashboard.py
   def get_dashboard_context():
       # ... existing code ...
       
       # Monthly requests data
       from django.db.models import Count
       from django.utils import timezone
       from datetime import timedelta
       
       monthly_data = []
       months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
       
       for i in range(12):
           month_start = timezone.now().replace(day=1) - timedelta(days=30 * (11 - i))
           month_end = month_start + timedelta(days=30)
           count = AdRequest.objects.filter(
               created_at__gte=month_start,
               created_at__lt=month_end
           ).count()
           monthly_data.append({
               'month': months[month_start.month - 1],
               'requests': count
           })
       
       return {
           # ... existing context ...
           'monthly_requests_data': monthly_data,
       }
   ```

   Then in the template:

   ```html
   <script>
   const monthlyData = {{ monthly_requests_data|safe }};
   window.MonthlyRequestsChartJS.init('monthlyRequestsChart', {
     darkMode: document.body.classList.contains('theme-dark'),
     data: monthlyData
   });
   </script>
   ```

## Option 2: React + Recharts Implementation

**Files:** 
- `static/js/MonthlyRequestsChart.jsx` (React component)
- `static/js/monthly-requests-chart.js` (Standalone JS wrapper)

### Prerequisites

You'll need to include React, ReactDOM, and Recharts via CDN or set up a build process.

### CDN Integration (Quick Setup)

Add to your `base_page.html` before closing `</head>`:

```html
<!-- React & ReactDOM -->
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>

<!-- Recharts -->
<script src="https://unpkg.com/recharts@2.10.3/dist/Recharts.js"></script>

<!-- Tailwind CSS -->
<script src="https://cdn.tailwindcss.com"></script>
```

Then add to dashboard.html:

```html
<div id="monthlyRequestsChartReact"></div>
<script src="{% static 'js/monthly-requests-chart.js' %}"></script>
<script>
  // Initialize React component
  const monthlyData = window.MonthlyRequestsChart.generateMockData();
  window.MonthlyRequestsChart.init('monthlyRequestsChartReact', {
    darkMode: document.body.classList.contains('theme-dark'),
    data: monthlyData
  });
</script>
```

### Build Process (Recommended for Production)

1. Install dependencies:

   ```bash
   npm install react react-dom recharts
   npm install --save-dev @babel/core @babel/preset-react webpack webpack-cli babel-loader
   ```

2. Create `webpack.config.js`:

   ```javascript
   module.exports = {
     entry: './static/js/MonthlyRequestsChart.jsx',
     output: {
       path: __dirname + '/static/js/dist',
       filename: 'monthly-requests-chart.bundle.js'
     },
     module: {
       rules: [{
         test: /\.jsx$/,
         exclude: /node_modules/,
         use: {
           loader: 'babel-loader',
           options: {
             presets: ['@babel/preset-react']
           }
         }
       }]
     }
   };
   ```

3. Build:

   ```bash
   npx webpack --mode production
   ```

4. Include in template:

   ```html
   <script src="{% static 'js/dist/monthly-requests-chart.bundle.js' %}"></script>
   ```

## Customization

### Styling

The chart automatically adapts to your theme using CSS variables:
- `--bg-card`: Chart container background
- `--border-color`: Border color
- `--text-primary`: Text color
- `--text-secondary`: Secondary text color

### Colors

Default colors:
- Primary: `#6366f1` (indigo-500)
- Dark mode grid: `rgba(255, 255, 255, 0.1)`
- Light mode grid: `rgba(0, 0, 0, 0.1)`

To customize, edit the `initMonthlyRequestsChart` function in the JS file.

### Animation

- Duration: 1500ms
- Easing: `cubic-bezier(0.4, 0, 0.2, 1)`

To change, modify the `animation.duration` and `animation.easing` options in Chart.js config.

## Browser Support

- Chart.js: Modern browsers (Chrome, Firefox, Safari, Edge)
- React/Recharts: ES6+ browsers

## Troubleshooting

1. **Chart not appearing:**
   - Check browser console for errors
   - Ensure Chart.js is loaded before the script
   - Verify container ID matches

2. **Dark mode not working:**
   - Ensure `theme-dark` class is on `<body>` tag
   - Check CSS variables are defined in `style.css`

3. **Animation not smooth:**
   - Check browser supports CSS animations
   - Verify Chart.js version is 4.x+

## Example Usage

See `templates/core/dashboard_monthly_chart.html` for a complete example.
