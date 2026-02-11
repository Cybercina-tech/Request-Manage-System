import React, { useState, useEffect, useRef } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine
} from 'recharts';

const MonthlyRequestsChart = ({ data, darkMode = false }) => {
  const [totalRequests, setTotalRequests] = useState(0);
  const [isAnimating, setIsAnimating] = useState(true);
  const [activeIndex, setActiveIndex] = useState(null);
  const [mousePosition, setMousePosition] = useState(null);
  const chartRef = useRef(null);
  const animationRef = useRef(null);

  // Calculate total requests
  const total = data.reduce((sum, item) => sum + item.requests, 0);

  // Count-up animation on mount
  useEffect(() => {
    const duration = 1500;
    const steps = 60;
    const increment = total / steps;
    const stepDuration = duration / steps;
    let current = 0;
    let step = 0;

    const animate = () => {
      step++;
      current = Math.min(increment * step, total);
      setTotalRequests(Math.floor(current));

      if (step < steps) {
        animationRef.current = setTimeout(animate, stepDuration);
      } else {
        setTotalRequests(total);
        setIsAnimating(false);
      }
    };

    animate();

    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, [total]);

  // Custom Tooltip Component with Glassmorphism
  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;

    const value = payload[0].value;
    const month = label;

    return (
      <div
        className={`absolute z-50 pointer-events-none ${
          darkMode
            ? 'bg-black/40 backdrop-blur-md border border-white/20'
            : 'bg-white/80 backdrop-blur-md border border-gray-200/50'
        } rounded-lg shadow-xl px-4 py-3`}
        style={{
          left: '50%',
          transform: 'translateX(-50%)',
          bottom: '100%',
          marginBottom: '8px',
        }}
      >
        <div className={`text-sm font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`}>
          {month}
        </div>
        <div className={`text-lg font-bold ${darkMode ? 'text-indigo-400' : 'text-indigo-600'}`}>
          {value.toLocaleString()} requests
        </div>
      </div>
    );
  };

  // Handle mouse move for crosshair
  const handleMouseMove = (e) => {
    if (!chartRef.current) return;
    const rect = chartRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    setMousePosition(x);
  };

  const handleMouseLeave = () => {
    setMousePosition(null);
    setActiveIndex(null);
  };

  // Gradient definition for area fill
  const gradientId = 'areaGradient';
  const gradientColor = darkMode ? '#6366f1' : '#6366f1'; // indigo-500

  return (
    <div
      className={`w-full h-full ${darkMode ? 'bg-gray-900 text-white' : 'bg-white text-gray-900'}`}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    >
      {/* Header with Total Requests */}
      <div className="mb-6">
        <h2 className={`text-2xl font-bold mb-2 ${darkMode ? 'text-white' : 'text-gray-900'}`}>
          Monthly Requests
        </h2>
        <div className="flex items-baseline gap-2">
          <span className={`text-4xl font-extrabold ${darkMode ? 'text-indigo-400' : 'text-indigo-600'}`}>
            {totalRequests.toLocaleString()}
          </span>
          <span className={`text-lg ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
            Total Requests
          </span>
        </div>
      </div>

      {/* Chart Container */}
      <div className="relative" ref={chartRef}>
        <ResponsiveContainer width="100%" height={400}>
          <AreaChart
            data={data}
            margin={{ top: 10, right: 30, left: 0, bottom: 0 }}
            onMouseMove={(e) => {
              if (e && e.activeTooltipIndex !== undefined) {
                setActiveIndex(e.activeTooltipIndex);
              }
            }}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={gradientColor}
                  stopOpacity={darkMode ? 0.6 : 0.4}
                />
                <stop
                  offset="100%"
                  stopColor={gradientColor}
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke={darkMode ? '#374151' : '#e5e7eb'}
              opacity={0.5}
            />
            <XAxis
              dataKey="month"
              stroke={darkMode ? '#9ca3af' : '#6b7280'}
              tick={{ fill: darkMode ? '#9ca3af' : '#6b7280' }}
              style={{ fontSize: '12px' }}
            />
            <YAxis
              stroke={darkMode ? '#9ca3af' : '#6b7280'}
              tick={{ fill: darkMode ? '#9ca3af' : '#6b7280' }}
              style={{ fontSize: '12px' }}
              tickFormatter={(value) => value.toLocaleString()}
            />
            <Tooltip content={<CustomTooltip darkMode={darkMode} />} />
            <Area
              type="monotone"
              dataKey="requests"
              stroke={gradientColor}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{
                r: 6,
                fill: gradientColor,
                stroke: darkMode ? '#1f2937' : '#ffffff',
                strokeWidth: 2,
                style: { filter: 'drop-shadow(0 4px 6px rgba(0, 0, 0, 0.1))' },
              }}
              animationBegin={0}
              animationDuration={1500}
              animationEasing="cubic-bezier(0.4, 0, 0.2, 1)"
            />
            {/* Crosshair Reference Line */}
            {mousePosition !== null && activeIndex !== null && (
              <ReferenceLine
                x={data[activeIndex]?.month}
                stroke={darkMode ? '#6366f1' : '#6366f1'}
                strokeWidth={1}
                strokeDasharray="5 5"
                opacity={0.5}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default MonthlyRequestsChart;
