//! Adversarial stress tests for Batching & Render Target Concurrency.
use renpy_host::arena::{GpuArena, INSTANCE_FLOATS};
use renpy_host::event_queue::{types, EventQueue, HostEvent};
use renpy_host::state::HostState;
use renpy_host::timer::TimerKind;
use std::sync::{Arc, Barrier, Mutex};
use std::thread;

/// 1. Stress test GpuArena draw_instances with massive burst, buffer limit calculation, and boundary validation
#[test]
fn test_adversarial_draw_instances_massive_burst_and_bounds() {
    let mut arena = GpuArena::new();
    
    // Test 1a: draw_instances before begin_frame should log warning and not crash
    let quad_data = vec![0.0f32; INSTANCE_FLOATS * 10];
    let res = arena.draw_instances(1, Some(10), None, None, &quad_data);
    assert!(res.is_ok());

    // Begin top-level frame
    arena.begin_frame();
    assert_eq!(arena.frame_depth(), 1);

    // Test 1b: Empty instances should be safe no-op
    assert!(arena.draw_instances(1, Some(10), None, None, &[]).is_ok());

    // Test 1c: Invalid length (not multiple of INSTANCE_FLOATS) should fail closed with Err
    let invalid_len_data = vec![1.0f32; INSTANCE_FLOATS * 3 + 5];
    let err_res = arena.draw_instances(1, Some(10), None, None, &invalid_len_data);
    assert!(err_res.is_err());
    assert!(err_res.unwrap_err().contains("not multiple of"));

    // Test 1d: Massive burst of 20,000 quads (240,000 floats = ~960 KB)
    let quad_count = 20_000;
    let mut massive_data = Vec::with_capacity(quad_count * INSTANCE_FLOATS);
    for i in 0..quad_count {
        let x = (i % 100) as f32;
        let y = (i / 100) as f32;
        // 12 floats: [rox, roy, rsx, rsy, uox, voy, usx, vsy, cr, cg, cb, ca]
        massive_data.extend_from_slice(&[x, y, 10.0, 10.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]);
    }
    
    let burst_res = arena.draw_instances(100, Some(200), None, None, &massive_data);
    assert!(burst_res.is_ok());

    // Reset frame state cleans up cleanly
    arena.reset_frame_state();
    assert_eq!(arena.frame_depth(), 0);
}

/// 2. Stress test deep nested RTT passes and target stack integrity (100 levels)
#[test]
fn test_adversarial_deep_nested_rtt_cycles() {
    let mut arena = GpuArena::new();
    
    let nest_depth = 100;
    for depth in 1..=nest_depth {
        arena.begin_frame();
        assert_eq!(arena.frame_depth(), depth as u32);
        
        // Add a mock instance command at this depth
        let quad = vec![0.0f32; INSTANCE_FLOATS];
        let _ = arena.draw_instances(1, None, None, None, &quad);
    }

    assert_eq!(arena.frame_depth(), 100);

    // Test that reset_frame_state completely clears all 100 levels safely
    arena.reset_frame_state();
    assert_eq!(arena.frame_depth(), 0);
    assert!(arena.active_target.is_none());
}

/// 3. Stress test error unwinding mid-nest and memory cleanup
#[test]
fn test_adversarial_nested_rtt_error_unwinding() {
    let mut arena = GpuArena::new();
    
    // Simulate nested execution: depth 5
    for _ in 0..5 {
        arena.begin_frame();
        let data = vec![0.5f32; INSTANCE_FLOATS * 4];
        let _ = arena.draw_instances(2, None, None, None, &data);
    }
    assert_eq!(arena.frame_depth(), 5);

    // Simulate an unexpected error/unwind in python preparation -> reset_frame_state()
    arena.reset_frame_state();
    assert_eq!(arena.frame_depth(), 0);
    assert!(arena.active_target.is_none());

    // Subsequent normal begin_frame works cleanly without residual state
    arena.begin_frame();
    assert_eq!(arena.frame_depth(), 1);
    arena.reset_frame_state();
    assert_eq!(arena.frame_depth(), 0);
}

/// 4. Multi-threaded stress testing of HostState, TimerWheel, EventQueue, and GpuArena
#[test]
fn test_adversarial_multithreaded_concurrency_stress() {
    let host_state = Arc::new(Mutex::new(HostState::new()));
    let eq = Arc::new(EventQueue::new());
    let num_threads = 16;
    let iterations = 1000;
    let barrier = Arc::new(Barrier::new(num_threads));

    let mut handles = Vec::new();

    for thread_idx in 0..num_threads {
        let state_arc = Arc::clone(&host_state);
        let eq_arc = Arc::clone(&eq);
        let bar = Arc::clone(&barrier);

        let handle = thread::spawn(move || {
            bar.wait();
            for i in 0..iterations {
                let mut state = state_arc.lock().expect("mutex lock poisoned");
                
                match thread_idx % 4 {
                    0 => {
                        // Thread type 0: frame lifecycle & statistics
                        state.frames = state.frames.saturating_add(1);
                        let _stats = state.arena.last_frame_stats();
                        let _depth = state.arena.frame_depth();
                    }
                    1 => {
                        // Thread type 1: event queue operations
                        eq_arc.push(HostEvent::simple(types::KEYDOWN));
                        let _ = eq_arc.poll();
                    }
                    2 => {
                        // Thread type 2: timer wheel operations
                        let timer_id = state.timers.set_timer((thread_idx * 100 + (i % 10)) as u32, 50, TimerKind::Periodic, true);
                        if timer_id > 0 {
                            state.timers.set_timer((thread_idx * 100 + (i % 10)) as u32, 0, TimerKind::Periodic, true);
                        }
                    }
                    3 => {
                        // Thread type 3: arena state checks & resets
                        if state.arena.frame_depth() == 0 {
                            state.arena.begin_frame();
                            let data = vec![1.0f32; INSTANCE_FLOATS * 2];
                            let _ = state.arena.draw_instances(1, None, None, None, &data);
                            state.arena.reset_frame_state();
                        }
                    }
                    _ => unreachable!(),
                }
            }
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().expect("thread join failed");
    }

    let final_state = host_state.lock().expect("mutex lock poisoned");
    assert!(final_state.frames > 0);
    assert_eq!(final_state.arena.frame_depth(), 0);
}
