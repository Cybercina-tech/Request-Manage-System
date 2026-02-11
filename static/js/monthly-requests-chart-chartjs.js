/**
 * Monthly Requests Chart - Chart.js Implementation
 * Modern, responsive area chart with animations and interactivity
 * Compatible with existing Chart.js setup in the project
 */

(function() {
  'use strict';

  // Generate mock data (12 months, Jan-Dec, 1000-5000 range)
  function generateMockData() {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return months.map((month) => ({
      month: month,
      requests: Math.floor(Math.random() * 4000) + 1000, // 1000-5000 range
    }));
  }

  // Count-up animation
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

  // Initialize Chart.js chart
  function initMonthlyRequestsChart(containerId, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.error('Container element not found:', containerId);
      return;
    }

    if (typeof Chart === 'undefined') {
      container.innerHTML = '<div class="text-red-500 p-4">Chart.js library required.</div>';
      return;
    }

    const darkMode = options.darkMode !== undefined ? options.darkMode : 
                     document.body.classList.contains('theme-dark');
    const data = options.data || generateMockData();
    const total = data.reduce((sum, item) => sum + item.requests, 0);

    // Theme colors
    const primaryColor = darkMode ? '#6366f1' : '#6366f1'; // indigo-500
    const textColor = darkMode ? '#f3f4f6' : '#111827';
    const gridColor = darkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)';
    const bgColor = darkMode ? '#111827' : '#ffffff';

    // Create header HTML
    const headerHTML = `
      <div class="mb-6">
        <h2 class="text-2xl font-bold mb-2" style="color: ${textColor}">Monthly Requests</h2>
        <div class="flex items-baseline gap-2">
          <span class="text-4xl font-extrabold" id="${containerId}-total" style="color: ${primaryColor}">0</span>
          <span class="text-lg" style="color: ${darkMode ? '#9ca3af' : '#6b7280'}">Total Requests</span>
        </div>
      </div>
    `;

    // Create canvas container
    const canvasContainer = document.createElement('div');
    canvasContainer.className = 'relative';
    canvasContainer.style.height = '400px';
    canvasContainer.style.position = 'relative';

    const canvas = document.createElement('canvas');
    canvas.id = containerId + '-canvas';

    container.innerHTML = headerHTML;
    canvasContainer.appendChild(canvas);
    container.appendChild(canvasContainer);

    // Animate counter
    const totalElement = document.getElementById(containerId + '-total');
    if (totalElement) {
      animateCounter(totalElement, total, 1500);
    }

    // Create gradient
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, darkMode ? 'rgba(99, 102, 241, 0.6)' : 'rgba(99, 102, 241, 0.4)');
    gradient.addColorStop(1, 'rgba(99, 102, 241, 0)');

    // Chart configuration
    const chartConfig = {
      type: 'line',
      data: {
        labels: data.map(d => d.month),
        datasets: [{
          label: 'Requests',
          data: data.map(d => d.requests),
          borderColor: primaryColor,
          backgroundColor: gradient,
          borderWidth: 2,
          fill: true,
          tension: 0.4, // Smooth curve (monotone-like)
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: primaryColor,
          pointHoverBorderColor: darkMode ? '#1f2937' : '#ffffff',
          pointHoverBorderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 1500,
          easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
        },
        interaction: {
          intersect: false,
          mode: 'index',
        },
        plugins: {
          legend: {
            display: false
          },
          tooltip: {
            backgroundColor: darkMode 
              ? 'rgba(0, 0, 0, 0.8)' 
              : 'rgba(255, 255, 255, 0.95)',
            backdropBlur: '10px',
            backdropPadding: 10,
            padding: 12,
            borderColor: darkMode ? 'rgba(255, 255, 255, 0.2)' : 'rgba(0, 0, 0, 0.1)',
            borderWidth: 1,
            titleColor: darkMode ? '#f3f4f6' : '#111827',
            bodyColor: darkMode ? '#6366f1' : '#6366f1',
            titleFont: {
              size: 14,
              weight: '600'
            },
            bodyFont: {
              size: 16,
              weight: '700'
            },
            callbacks: {
              label: function(context) {
                return context.parsed.y.toLocaleString() + ' requests';
              }
            },
            displayColors: false,
          }
        },
        scales: {
          x: {
            grid: {
              display: false
            },
            ticks: {
              color: darkMode ? '#9ca3af' : '#6b7280',
              font: {
                size: 12
              }
            }
          },
          y: {
            grid: {
              color: gridColor,
              drawBorder: false
            },
            ticks: {
              color: darkMode ? '#9ca3af' : '#6b7280',
              font: {
                size: 12
              },
              callback: function(value) {
                return value.toLocaleString();
              }
            }
          }
        },
        onHover: (event, activeElements) => {
          // Crosshair effect handled by Chart.js default behavior
          canvas.style.cursor = activeElements.length > 0 ? 'pointer' : 'default';
        }
      }
    };

    // Create chart
    const chart = new Chart(ctx, chartConfig);

    // Store chart instance for potential updates
    window[containerId + '_chart'] = chart;

    return chart;
  }

  // Export to global scope
  window.MonthlyRequestsChartJS = {
    init: initMonthlyRequestsChart,
    generateMockData: generateMockData
  };
})();
