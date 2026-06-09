import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ConfidenceBar } from '@/components/ConfidenceBar';

describe('ConfidenceBar Component', () => {
  it('renders correctly with confidence value', () => {
    render(<ConfidenceBar value={0.85} />);
    
    // Check if confidence label is rendered
    expect(screen.getByText('Confidence')).toBeInTheDocument();
    
    // Check if the calculated percentage is rendered
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('rounds decimal confidence value correctly', () => {
    render(<ConfidenceBar value={0.726} />);
    expect(screen.getByText('73%')).toBeInTheDocument();
  });

  it('enforces a minimum visual width of 4%', () => {
    const { container } = render(<ConfidenceBar value={0.01} />);
    
    // Check that text shows 1%
    expect(screen.getByText('1%')).toBeInTheDocument();
    
    // The bar should use Math.max(4, percent) -> 4%
    const bar = container.querySelector('.bg-copper');
    expect(bar).toHaveStyle({ width: '4%' });
  });

  it('renders correct width for standard confidence', () => {
    const { container } = render(<ConfidenceBar value={0.65} />);
    
    // The bar should use Math.max(4, percent) -> 65%
    const bar = container.querySelector('.bg-copper');
    expect(bar).toHaveStyle({ width: '65%' });
  });
});
