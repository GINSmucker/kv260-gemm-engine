// Read-only AXI traffic observation plus an idle-only statistics clear.
// This block never changes GEMM requests, responses, clocks or resets.
module f8_status #(
    parameter [31:0] BUILD_TAG0 = 0,
    parameter [31:0] BUILD_TAG1 = 0
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET aresetn" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST", X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR", X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 7, READ_WRITE_MODE READ_WRITE" *)
    input wire [6:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *) input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *) input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *) output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *) input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *) input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *) input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *) output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *) output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *) output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *) input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *) input wire [6:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *) input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *) input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *) output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *) output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *) output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *) output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *) input wire s_axi_rready,
    input wire x_arvalid, input wire x_arready,
    input wire x_rvalid, input wire x_rready, input wire x_rlast, input wire [1:0] x_rresp,
    input wire w_arvalid, input wire w_arready,
    input wire w_rvalid, input wire w_rready, input wire w_rlast, input wire [1:0] w_rresp,
    input wire y_awvalid, input wire y_awready,
    input wire y_wvalid, input wire y_wready,
    input wire y_bvalid, input wire y_bready, input wire [1:0] y_bresp
);
    reg have_aw, have_w;
    reg [6:0] awaddr;
    reg [31:0] wdata;
    reg [3:0] wstrb;
    reg [4:0] errors;
    reg [15:0] x_pending, w_pending, y_pending;
    reg [31:0] x_requests, x_beats, w_requests, w_beats, y_requests, y_beats, y_responses;
    wire xa = x_arvalid && x_arready;
    wire xr = x_rvalid && x_rready;
    wire wa = w_arvalid && w_arready;
    wire wr = w_rvalid && w_rready;
    wire ya = y_awvalid && y_awready;
    wire yw = y_wvalid && y_wready;
    wire yb = y_bvalid && y_bready;
    // Instantaneous bus status; software must also establish kernel idle and
    // exclusive ownership before treating this as a DMA lifetime boundary.
    wire bus_quiet = (x_pending == 0) && (w_pending == 0) && (y_pending == 0) &&
                     !x_arvalid && !w_arvalid && !y_awvalid && !y_wvalid &&
                     !x_rvalid && !w_rvalid && !y_bvalid;
    wire write_commit = have_aw && have_w && !s_axi_bvalid;
    wire clear_ok = write_commit && awaddr == 7'h40 && wstrb == 4'hf &&
                    wdata == 32'h434c5245 && bus_quiet;
    assign s_axi_awready = !have_aw && !s_axi_bvalid;
    assign s_axi_wready = !have_w && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid;

    always @(posedge aclk) begin
        if (!aresetn) begin
            have_aw <= 0; have_w <= 0; awaddr <= 0; wdata <= 0; wstrb <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rdata <= 0; s_axi_rresp <= 0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin have_aw <= 1; awaddr <= s_axi_awaddr; end
            if (s_axi_wvalid && s_axi_wready) begin have_w <= 1; wdata <= s_axi_wdata; wstrb <= s_axi_wstrb; end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (write_commit) begin
                have_aw <= 0; have_w <= 0; s_axi_bvalid <= 1;
                s_axi_bresp <= clear_ok ? 2'b00 : 2'b10;
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1; s_axi_rresp <= 0;
                case (s_axi_araddr)
                    7'h00: s_axi_rdata <= 32'h46383031; // F801
                    7'h04: s_axi_rdata <= 32'h00010000;
                    7'h08: s_axi_rdata <= BUILD_TAG0;
                    7'h0c: s_axi_rdata <= BUILD_TAG1;
                    7'h10: s_axi_rdata <= {27'b0, errors};
                    7'h14: s_axi_rdata <= {16'b0, x_pending};
                    7'h18: s_axi_rdata <= {16'b0, w_pending};
                    7'h1c: s_axi_rdata <= {16'b0, y_pending};
                    7'h20: s_axi_rdata <= x_requests;
                    7'h24: s_axi_rdata <= x_beats;
                    7'h28: s_axi_rdata <= w_requests;
                    7'h2c: s_axi_rdata <= w_beats;
                    7'h30: s_axi_rdata <= y_requests;
                    7'h34: s_axi_rdata <= y_beats;
                    7'h38: s_axi_rdata <= y_responses;
                    7'h3c: s_axi_rdata <= {31'b0, bus_quiet};
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                endcase
            end
        end
    end

    always @(posedge aclk) begin
        if (!aresetn || clear_ok) begin
            errors <= 0;
            x_pending <= 0; w_pending <= 0; y_pending <= 0;
            x_requests <= 0; x_beats <= 0; w_requests <= 0; w_beats <= 0;
            y_requests <= 0; y_beats <= 0; y_responses <= 0;
        end else begin
            if (xr && x_rresp[1]) errors[0] <= 1;
            if (wr && w_rresp[1]) errors[1] <= 1;
            if (yb && y_bresp[1]) errors[2] <= 1;
            if (write_commit && !clear_ok) errors[4] <= 1;
            case ({xa, xr && x_rlast})
                2'b10: if (x_pending == 16'hffff) errors[3] <= 1; else x_pending <= x_pending + 1'b1;
                2'b01: if (x_pending == 0) errors[3] <= 1; else x_pending <= x_pending - 1'b1;
                default: ;
            endcase
            case ({wa, wr && w_rlast})
                2'b10: if (w_pending == 16'hffff) errors[3] <= 1; else w_pending <= w_pending + 1'b1;
                2'b01: if (w_pending == 0) errors[3] <= 1; else w_pending <= w_pending - 1'b1;
                default: ;
            endcase
            case ({ya, yb})
                2'b10: if (y_pending == 16'hffff) errors[3] <= 1; else y_pending <= y_pending + 1'b1;
                2'b01: if (y_pending == 0) errors[3] <= 1; else y_pending <= y_pending - 1'b1;
                default: ;
            endcase
            if (xa) x_requests <= x_requests + 1'b1;
            if (xr) x_beats <= x_beats + 1'b1;
            if (wa) w_requests <= w_requests + 1'b1;
            if (wr) w_beats <= w_beats + 1'b1;
            if (ya) y_requests <= y_requests + 1'b1;
            if (yw) y_beats <= y_beats + 1'b1;
            if (yb) y_responses <= y_responses + 1'b1;
        end
    end
endmodule
