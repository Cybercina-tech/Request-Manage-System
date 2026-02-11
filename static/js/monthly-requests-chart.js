/**
 * Monthly Requests Chart Component
 * Standalone JavaScript version using Recharts via CDN
 * Compatible with Django templates - no build step required
 */

(function() {
  'use strict';

  // Mock data generator (12 months, Jan-Dec, 1000-5000 range)
  function generateMockData() {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return months.map((month, index) => ({
      month: month,
      requests: Math.floor(Math.random() * 4000) + 1000, // 1000-5000 range
    }));
  }

  // Count-up animation utility
  function animateCounter(element, target, duration = 1500) {
    const steps = 60;
    const increment = target / steps;
    const stepDuration = duration / steps;
    let current = 0;
    let step = 0;

    function animate() {
      step++;
      current = Math.min(increment * step, target);
      element.textContent = Math.floor(current).toLocaleString();

      if (step < steps) {
        setTimeout(animate, stepDuration);
      } else {
        element.textContent = target.toLocaleString();
      }
    }

    animate();
  }

  // Initialize chart when DOM is ready
  function initMonthlyRequestsChart(containerId, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.error('Container element not found:', containerId);
      return;
    }

    const darkMode = options.darkMode !== undefined ? options.darkMode : 
                     document.body.classList.contains('theme-dark');
    const data = options.data || generateMockData();
    const total = data.reduce((sum, item) => sum + item.requests, 0);

    // Check if Recharts is loaded
    if (typeof Recharts === 'undefined') {
      console.error('Recharts library not loaded. Please include Recharts via CDN.');
      container.innerHTML = '<div class="text-red-500 p-4">Recharts library required. Please include the Recharts CDN script.</div>';
      return;
    }

    const { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } = Recharts;

    // Create header with animated counter
    const headerHTML = `
      <div class="mb-6">
        <h2 class="text-2xl font-bold mb-2 ${darkMode ? 'text-white' : 'text-gray-900'}">Monthly Requests</h2>
        <div class="flex items-baseline gap-2">
          <span class="text-4xl font-extrabold ${darkMode ? 'text-indigo-400' : 'text-indigo-600'}" id="${containerId}-total">0</span>
          <span class="text-lg ${darkMode ? 'text-gray-400' : 'text-gray-600'}">Total Requests</span>
        </div>
      </div>
    `;

    // Create chart container
    const chartContainer = document.createElement('div');
    chartContainer.className = 'relative';
    chartContainer.id = containerId + '-chart';

    container.innerHTML = headerHTML;
    container.appendChild(chartContainer);

    // Animate counter
    const totalElement = document.getElementById(containerId + '-total');
    if (totalElement) {
      animateCounter(totalElement, total, 1500);
    }

    // Render chart
    const gradientId = containerId + '-gradient';
    const gradientColor = '#6366f1'; // indigo-500

    // Custom Tooltip Component
    const CustomTooltip = ({ active, payload, label }) => {
      if (!active || !payload || !payload.length) return null;
      const value = payload[0].value;
      return React.createElement('div', {
        className: `absolute z-50 pointer-events-none ${
          darkMode
            ? 'bg-black/40 backdrop-blur-md border border-white/20'
            : 'bg-white/80 backdrop-blur-md border border-gray-200/50'
        } rounded-lg shadow-xl px-4 py-3`,
        style: {
          left: '50%',
          transform: 'translateX(-50%)',
          bottom: '100%',
          marginBottom: '8px',
        }
      }, [
        React.createElement('div', {
          key: 'month',
          className: `text-sm font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`
        }, label),
        React.createElement('div', {
          key: 'value',
          className: `text-lg font-bold ${darkMode ? 'text-indigo-400' : 'text-indigo-600'}`
        }, value.toLocaleString() + ' requests')
      ]);
    };

    // Render with React and Recharts
    const root = ReactDOM.createRoot(chartContainer);
    root.render(
      React.createElement(ResponsiveContainer, { width: '100%', height: 400 },
        React.createElement(AreaChart, {
          data: data,
          margin: { top: 10, right: 30, left: 0, bottom: 0 },
        },
          React.createElement('defs', {},
            React.createElement('linearGradient', { id: gradientId, x1: '0', y1: '0', x2: '0', y2: '1' },
              React.createElement('stop', {
                offset: '0%',
                stopColor: gradientColor,
                stopOpacity: darkMode ? 0.6 : 0.4
              }),
              React.createElement('stop', {
                offset: '100%',
                stopColor: gradientColor,
                stopOpacity: 0
              })
            )
          ),
          React.createElement(CartesianGrid, {
            strokeDasharray: '3 3',
            stroke: darkMode ? '#374151' : '#e5e7eb',
            opacity: 0.5
          }),
          React.createElement(XAxis, {
            dataKey: 'month',
            stroke: darkMode ? '#9ca3af' : '#6b7280',
            tick: { fill: darkMode ? '#9ca3af' : '#6b7280', style: { fontSize: '12px' } }
          }),
          React.createElement(YAxis, {
            stroke: darkMode ? '#9ca3af' : '#6b7280',
            tick: { fill: darkMode ? '#9ca3af' : '#6b7280', style: { fontSize: '12px' } },
            tickFormatter: (value) => value.toLocaleString()
          }),
          React.createElement(Tooltip, { content: React.createElement(CustomTooltip, { darkMode: darkMode }) }),
          React.createElement(Area, {
            type: 'monotone',
            dataKey: 'requests',
            stroke: gradientColor,
            strokeWidth: 2,
            fill: `url(#${gradientId})`,
            dot: false,
            activeDot: {
              r: 6,
              fill: gradientColor,
              stroke: darkMode ? '#1f2937' : '#ffffff',
              strokeWidth: 2,
              style: { filter: 'drop-shadow(0 4px 6px rgba(0, 0, 0, 0.1))' }
            },
            animationBegin: 0,
            animationDuration: 1500,
            animationEasing: 'cubic-bezier(0.4, 0, 0.2, 1)'
          })
        )
      )
    );
  }

  // Export to global scope for use in templates
  window.MonthlyRequestsChart = {
    init: initMonthlyRequestsChart,
    generateMockData: generateMockData
  };
})();
