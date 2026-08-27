pub mod arena;
pub mod audio;
pub mod audio_mixer;
pub mod config;
pub mod event_queue;
pub mod gpu;
pub mod input_trace;
pub mod pump;
pub mod python;
pub mod shader;
pub mod state;
pub mod timer;
pub mod video;

/// Computes the average duration across `count` iterations using nanoseconds conversion with divide-by-zero protection.
pub fn calculate_avg_duration(total: std::time::Duration, count: u64) -> std::time::Duration {
    if count > 0 {
        let total_nanos = total.as_nanos();
        let avg_nanos = total_nanos / (count as u128);
        std::time::Duration::from_nanos(avg_nanos as u64)
    } else {
        std::time::Duration::ZERO
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn test_nanosecond_benchmark_calculation_and_zero_count_safety() {
        // Zero count returns Duration::ZERO safely without panicking
        assert_eq!(
            calculate_avg_duration(Duration::from_millis(500), 0),
            Duration::ZERO
        );
        assert_eq!(
            calculate_avg_duration(Duration::from_nanos(0), 0),
            Duration::ZERO
        );

        // Non-zero count computes correct nanosecond precision
        let total = Duration::from_millis(500); // 500_000_000 ns
        let count = 5;
        let avg = calculate_avg_duration(total, count);
        assert_eq!(avg, Duration::from_millis(100));

        let total_odd = Duration::from_nanos(10);
        let count_odd = 3;
        let avg_odd = calculate_avg_duration(total_odd, count_odd);
        assert_eq!(avg_odd, Duration::from_nanos(3));
    }
}
