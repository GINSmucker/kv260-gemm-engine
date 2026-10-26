`timescale 1ns/1ps
// Behavioral simulation of the ACTUAL generated production block design.
// Only the vendor PS VIP APIs drive its PS clock/reset/AXI interfaces. There is
// no replacement reset model, force/release, or direct access to CSR internals.
// Two tiny cases plus 48 frozen real-layer RTL cases, not a board regression.
module tb_opt50_board_logic #(
    parameter logic [31:0] TAG0 = 32'hffffffff,
    parameter logic [31:0] TAG1 = 32'hffffffff
);
    kv260_opt50_wrapper dut();
    `define PSVIP dut.kv260_opt50_i.ps.inst
    wire clock = dut.kv260_opt50_i.ps_pl_clk0;
    wire external_reset_n = dut.kv260_opt50_i.ps_pl_resetn0;
    wire peripheral_reset_n = dut.kv260_opt50_i.reset.peripheral_aresetn;
    wire interconnect_reset_n = dut.kv260_opt50_i.reset.interconnect_aresetn;

    localparam logic [39:0] GEMM = 40'ha0000000, STATUS = 40'ha0010000;
    localparam logic [39:0] X_ADDR = 40'h00100000, W_ADDR = 40'h00200000;
    localparam logic [39:0] META_ADDR = 40'h00300000, Y_ADDR = 40'h00400000;
    integer unsigned checks = 0, pl_starts = 0;
    string phase = "initialization";
    bit finished = 0;

    // Observe the vendor front-door -> DDR-model commit request, without
    // driving/forcing any production or VIP signal. Retain on failure logs.
    always @(posedge `PSVIP.ddrc.sw_clk) begin
        if (`PSVIP.ddrc.wr_req && `PSVIP.ddrc.state == 0)
            $display("OPT50_VIP_DDR_COMMIT t=%0t addr=%h bytes=%0d data0=%h strb0=%h",
                     $time, `PSVIP.ddrc.wr_addr, `PSVIP.ddrc.wr_bytes,
                     `PSVIP.ddrc.wr_data[31:0], `PSVIP.ddrc.wr_strb[15:0]);
    end

    // Covers ALL blocking vendor APIs and missing clocks, including APIs that
    // do not return on an unresponsive AXI slave. This is simulation only.
    initial begin
        #500000000;
        if (!finished)
            $fatal(1, "OPT50_BOARD_LOGIC_RTL_FAIL global watchdog phase=%s checks=%0d", phase, checks);
    end

    task automatic demand(input logic condition, input string message);
        begin
            if (condition !== 1'b1)
                $fatal(1, "OPT50_BOARD_LOGIC_RTL_FAIL phase=%s: %s", phase, message);
            checks++;
        end
    endtask

    task automatic ticks(input integer count);
        repeat (count) @(posedge clock);
    endtask

    task automatic reset_asserted;
        begin
            demand(external_reset_n === 1'b0, "PS PL0 reset must be asserted");
            demand(peripheral_reset_n === 1'b0 && interconnect_reset_n === 1'b0,
                   "generated reset controller outputs must assert");
        end
    endtask

    task automatic wait_reset_release;
        integer i;
        begin
            for (i = 0; i < 4096; i++) begin
                @(posedge clock);
                if (peripheral_reset_n === 1'b1 && interconnect_reset_n === 1'b1)
                    break;
            end
            demand(i < 4096, "reset did not release; check auxiliary reset polarity/tie-off");
            demand(external_reset_n === 1'b1, "PS PL0 reset must be released");
            repeat (16) begin
                @(posedge clock);
                demand(peripheral_reset_n === 1'b1 && interconnect_reset_n === 1'b1,
                       "released reset must remain stable");
            end
        end
    endtask

    // In the inspected 2026.1 PS VIP _23, these arguments are 40-bit address,
    // max_transfer_bytes=256 (2048-bit data), and 2-bit response. Read a full
    // aligned 128-bit beat and select the CSR lane, avoiding ambiguous narrow
    // read placement in the vendor read_data helper on the 128-bit HPM port.
    task automatic csr_read(input logic [39:0] address, output logic [31:0] value);
        logic [2047:0] data;
        logic [1:0] response;
        bit done;
        begin
            data = 'x; response = 'x; done = 0;
            fork : bounded_csr_read
                begin
                    `PSVIP.read_data({address[39:4], 4'b0}, 16, data, response);
                    done = 1;
                end
                begin
                    ticks(8192);
                    if (!done) $fatal(1, "OPT50_BOARD_LOGIC_RTL_FAIL AXI read timeout address=%h phase=%s", address, phase);
                end
            join_any
            disable bounded_csr_read;
            demand(done && response === 2'b00, "HPM/control AXI read must return OKAY");
            value = data[8*address[3:0] +: 32];
        end
    endtask

    task automatic csr_write(input logic [39:0] address, input logic [31:0] value);
        logic [2047:0] data;
        logic [1:0] response;
        bit done;
        begin
            data = '0; data[31:0] = value; response = 'x; done = 0;
            fork : bounded_csr_write
                begin
                    `PSVIP.write_data(address, 4, data, response);
                    done = 1;
                end
                begin
                    ticks(8192);
                    if (!done) $fatal(1, "OPT50_BOARD_LOGIC_RTL_FAIL AXI write timeout address=%h phase=%s", address, phase);
                end
            join_any
            disable bounded_csr_write;
            demand(done && response === 2'b00, "HPM/control AXI write must return OKAY");
        end
    endtask

    task automatic read_equals(input logic [39:0] address, input logic [31:0] expected);
        logic [31:0] value;
        begin
            csr_read(address, value);
            demand(value === expected, $sformatf("CSR %h expected=%h actual=%h", address, expected, value));
        end
    endtask

    task automatic write_read(input logic [39:0] address, input logic [31:0] expected);
        begin
            csr_write(address, expected);
            read_equals(address, expected);
        end
    endtask

    task automatic check_idle;
        logic [31:0] control;
        begin
            csr_read(GEMM, control);
            demand((control & 32'h85) === 32'h04, "HLS must be idle with START/auto-restart clear");
            read_equals(STATUS + 'h10, 0);
            read_equals(STATUS + 'h14, 0);
            read_equals(STATUS + 'h18, 0);
            read_equals(STATUS + 'h1c, 0);
            read_equals(STATUS + 'h3c, 1);
        end
    endtask

    task automatic check_identity;
        begin
            read_equals(STATUS, 32'h46383031);
            read_equals(STATUS + 4, 32'h00010000);
            read_equals(STATUS + 8, TAG0);
            read_equals(STATUS + 12, TAG1);
            check_idle();
        end
    endtask

    task automatic reset_and_control_test;
        real first_edge, period;
        begin
            phase = "PS POR and PL reset overlap";
            // Matched to the installed PS VIP reset implementation and its
            // official mandatory POR example: POR values drive active-low nets.
            `PSVIP.por_srstb_reset(1'b1);
            `PSVIP.fpga_soft_reset(32'hf);
            #200;
            `PSVIP.por_srstb_reset(1'b0);
            `PSVIP.fpga_soft_reset(32'hf);
            ticks(64);
            reset_asserted();
            demand(dut.kv260_opt50_i.reset.aux_reset_in === 1'b0 &&
                   dut.kv260_opt50_i.reset.mb_debug_sys_rst === 1'b0 &&
                   dut.kv260_opt50_i.reset.dcm_locked === 1'b1,
                   "actual production reset constant connections differ");
            `PSVIP.por_srstb_reset(1'b1);
            ticks(32);
            reset_asserted();
            `PSVIP.fpga_soft_reset(32'h0);
            phase = "initial reset release";
            wait_reset_release();
            @(posedge clock); first_edge = $realtime;
            @(posedge clock); period = $realtime - first_edge;
            // The generated PS model uses 124.998749MHz due to PLL rounding.
            demand(period > 7.99 && period < 8.01, "production PL0 simulation clock must be approximately 125MHz");

            phase = "integrated HPM0 control read/write";
            check_identity();
            write_read(GEMM + 'h04, 0);
            write_read(GEMM + 'h08, 0);
            write_read(GEMM + 'h18, 32'h12345000);
            write_read(GEMM + 'h1c, 32'h00000008);
            write_read(GEMM + 'h24, 32'h00200000);
            write_read(GEMM + 'h28, 0);
            write_read(GEMM + 'h30, 32'h00300000);
            write_read(GEMM + 'h34, 0);
            write_read(GEMM + 'h3c, 32'h00400000);
            write_read(GEMM + 'h40, 0);
            write_read(GEMM + 'h48, 197);
            write_read(GEMM + 'h50, 576);
            write_read(GEMM + 'h58, 192);
            write_read(GEMM + 'h60, 1);
            csr_write(STATUS + 'h40, 32'h434c5245);
            check_idle();

            phase = "repeat external reset with PS reset overlap";
            `PSVIP.fpga_soft_reset(32'hf);
            ticks(64);
            reset_asserted();
            `PSVIP.por_srstb_reset(1'b0);
            ticks(32);
            reset_asserted();
            `PSVIP.por_srstb_reset(1'b1);
            ticks(32);
            reset_asserted();
            `PSVIP.fpga_soft_reset(32'h0);
            wait_reset_release();
            phase = "control reset defaults";
            check_identity();
            read_equals(GEMM + 'h18, 0);
            read_equals(GEMM + 'h1c, 0);
            read_equals(GEMM + 'h48, 0);
            read_equals(GEMM + 'h50, 0);
            read_equals(GEMM + 'h58, 0);
            read_equals(GEMM + 'h60, 0);
        end
    endtask

    task automatic matrix_test(input integer mode);
        // The installed PS VIP DDR backdoor is max_burst_bits=32768 bits.
        logic [32767:0] x_bytes, dense_bytes, sparse_bytes, metadata_bytes;
        logic [32767:0] initial_y, observed_y, readback_word, readback_output;
        logic [31:0] control, errors;
        integer expected [0:15];
        integer k, n, g, t, value, x_value, v0, v1, i;
        bit seen_done, completed;
        begin
            phase = mode ? "Sparse tiny integrated DDR case" : "Dense tiny integrated DDR case";
            x_bytes = '0; dense_bytes = '0; sparse_bytes = '0; metadata_bytes = '0;
            initial_y = '0; observed_y = 'x;
            for (k = 0; k < 16; k++) x_bytes[k*8 +: 8] = k - 8;
            for (n = 0; n < 16; n++) expected[n] = 0;
            for (g = 0; g < 4; g++) begin
                for (n = 0; n < 16; n++) begin
                    t = g*16 + n;
                    v0 = 1 + n%3;
                    v1 = -1 - g%2;
                    dense_bytes[((4*g)*16+n)*8 +: 8] = v0;
                    dense_bytes[((4*g+2)*16+n)*8 +: 8] = v1;
                    sparse_bytes[(2*t)*8 +: 8] = v0;
                    sparse_bytes[(2*t+1)*8 +: 8] = v1;
                    metadata_bytes[t*4 +: 4] = 4'h8; // p0=0, p1=2
                    expected[n] += (4*g-8)*v0 + (4*g+2-8)*v1;
                end
            end
            for (i = 0; i < 192; i++) initial_y[i*8 +: 8] = (i >= 64 && i < 128) ? 8'hcd : 8'ha5;
            `PSVIP.write_mem(x_bytes, X_ADDR, 16);
            if (mode) begin
                `PSVIP.write_mem(sparse_bytes, W_ADDR, 128);
                `PSVIP.write_mem(metadata_bytes, META_ADDR, 32);
            end else begin
                `PSVIP.write_mem(dense_bytes, W_ADDR, 256);
                `PSVIP.write_mem(32768'b0, META_ADDR, 16);
            end
            `PSVIP.write_mem(initial_y, Y_ADDR-64, 192);
            `PSVIP.read_mem(Y_ADDR-64, 192, observed_y);
            demand(observed_y[1535:0] === initial_y[1535:0], "DDR backdoor prefill/readback must agree before START");
            write_read(GEMM + 'h18, X_ADDR[31:0]);
            write_read(GEMM + 'h1c, 0);
            write_read(GEMM + 'h24, W_ADDR[31:0]);
            write_read(GEMM + 'h28, 0);
            write_read(GEMM + 'h30, META_ADDR[31:0]);
            write_read(GEMM + 'h34, 0);
            write_read(GEMM + 'h3c, Y_ADDR[31:0]);
            write_read(GEMM + 'h40, 0);
            write_read(GEMM + 'h48, 1);
            write_read(GEMM + 'h50, 16);
            write_read(GEMM + 'h58, 16);
            write_read(GEMM + 'h60, mode);
            check_idle();
            csr_write(STATUS + 'h40, 32'h434c5245);
            csr_read(GEMM, control); // clear any stale ap_done before START
            seen_done = 0; completed = 0;
            csr_write(GEMM, 1);
            pl_starts++;
            for (i = 0; i < 20000; i++) begin
                csr_read(GEMM, control);
                seen_done = seen_done || control[1];
                if (seen_done && ((control & 32'h85) == 32'h04)) begin
                    completed = 1;
                    break;
                end
                ticks(16);
            end
            demand(completed, "tiny HLS case did not complete within bounded polling");
            read_equals(GEMM + 'h10, 0);
            check_idle();
            // These are real monitored HLS->data SmartConnect->HP0 transfers.
            read_equals(STATUS + 'h24, 1);            // 16 bytes of X
            read_equals(STATUS + 'h2c, mode ? 10 : 16); // W + sparse metadata
            read_equals(STATUS + 'h34, 4);            // 64 bytes of INT32 Y
            `PSVIP.read_mem(Y_ADDR-64, 192, observed_y);
            `PSVIP.read_mem(Y_ADDR, 4, readback_word);
            `PSVIP.read_mem(Y_ADDR, 64, readback_output);
            $display("OPT50_VIP_DDR_READBACK mode=%0d direct4=%h direct64=%h guarded192=%h",
                     mode, readback_word[31:0], readback_output[511:0], observed_y[1535:0]);
            for (n = 0; n < 16; n++) begin
                value = $signed(observed_y[512+n*32 +: 32]);
                demand(value === expected[n], $sformatf("M1K16N16 mode=%0d n=%0d actual=%0d expected=%0d", mode, n, value, expected[n]));
            end
            for (i = 0; i < 64; i++) begin
                demand(observed_y[i*8 +: 8] === 8'ha5, "prefix DDR guard changed");
                demand(observed_y[(128+i)*8 +: 8] === 8'ha5, "suffix DDR guard changed");
            end
        end
    endtask

    `include "opt50_real_cases.svh"

    initial begin
        reset_and_control_test();
        `PSVIP.set_debug_level_info(1'b0);
        matrix_test(0);
        matrix_test(1);
        demand(pl_starts == 2, "exactly two tiny RTL kernel starts required");
        run_real_cases();
        demand(pl_starts == 50 && opt50_real_jobs == 48 && opt50_real_compared == 1870848,
               "full system exact case/output counts required");
        finished = 1;
        $display("OPT50_BOARD_LOGIC_RTL_PASS checks=%0d TAG0=%0d TAG1=%0d PL_STARTS=50 REAL_JOBS=48 INT32=1870848 MODEL=LOCAL_ARB_FIXED_V1", checks, TAG0, TAG1);
        $finish;
    end
    `undef PSVIP
endmodule
