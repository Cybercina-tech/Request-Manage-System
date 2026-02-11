# Monthly Requests Chart Component

A modern, responsive dashboard chart component displaying monthly request statistics with smooth animations and interactive features.

## Features

✅ **Area Chart** with smooth gradient fill (indigo-500 to transparent)  
✅ **Monotone curve** for organic, sleek appearance  
✅ **1500ms animation** on load with cubic-bezier easing  
✅ **Interactive tooltip** with glassmorphism background effect  
✅ **Active dot expansion** on hover  
✅ **Crosshair vertical line** following mouse cursor  
✅ **Dark mode compatible** - automatically adapts to theme  
✅ **Animated counter** - "Total Requests" counts up from 0 on mount  

## Quick Start

The chart is already integrated into `dashboard.html`. It will automatically:
- Detect dark/light theme from `<body>` class
- Generate mock data (12 months, 1000-5000 requests per month)
- Animate on page load

## Files Created

1. **`static/js/monthly-requests-chart-chartjs.js`** - Chart.js implementation (currently active)
2. **`static/js/MonthlyRequestsChart.jsx`** - React component (requires build setup)
3. **`static/js/monthly-requests-chart.js`** - React wrapper (CDN version)
4. **`docs/MONTHLY_CHART_INTEGRATION.md`** - Detailed integration guide

## Using Real Data

To use real data from your Django backend, modify `core/services/dashboard.py`:

```python
def get_dashboard_context():
    # ... existing code ...
    
    # Monthly requests data
    from django.db.models import Count
    from django.utils import timezone
    from datetime import timedelta
    
    monthly_data = []
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    
    now = timezone.now()
    for i in range(12):
        month_start = (now - timedelta(days=30 * (11 - i))).replace(day=1)
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

Then in `dashboard.html`, pass the data:

```html
<script>
const monthlyData = {{ monthly_requests_data|safe }};
if (window.MonthlyRequestsChartJS) {
  const darkMode = document.body.classList.contains('theme-dark');
  window.MonthlyRequestsChartJS.init('monthlyRequestsChart', {
    darkMode: darkMode,
    data: monthlyData
  });
}
</script>
```

## Customization

### Colors

Edit `static/js/monthly-requests-chart-chartjs.js`:

```javascript
const primaryColor = '#6366f1'; // Change to your brand color
```

### Animation Speed

```javascript
animation: {
  duration: 1500, // Change duration (ms)
  easing: 'cubic-bezier(0.4, 0, 0.2, 1)', // Change easing
}
```

### Counter Animation

```javascript
function animateCounter(element, target, duration = 1500) {
  // duration parameter controls counter speed
}
```

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

Requires Chart.js 4.x+ (already included in project).

## Troubleshooting

**Chart not appearing?**
- Check browser console for errors
- Ensure Chart.js is loaded (check Network tab)
- Verify container ID is `monthlyRequestsChart`

**Dark mode not working?**
- Ensure `<body>` has `theme-dark` or `theme-light` class
- Check CSS variables are defined in `style.css`

**Animation stuttering?**
- Reduce animation duration
- Check browser performance (disable extensions)

## React Version

For React + Recharts version, see `docs/MONTHLY_CHART_INTEGRATION.md` for setup instructions.
