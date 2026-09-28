`timescale 1ns/1ps
module tb_f8_status;
    reg clk=0;
    always #5 clk=~clk;
    reg resetn=0;
    reg [6:0] awaddr=0, araddr=0;
    reg [31:0] wdata=0;
    reg [3:0] wstrb=0;
    reg awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp;
    wire [31:0] rdata;
    reg xa=0,xr=0,xlast=0,wa=0,wr=0,wlast=0,ya=0,yw=0,yb=0;
    reg [1:0] xresp=0,wresp=0,yresp=0;
    integer checks=0;
    f8_status #(.BUILD_TAG0(32'h12345678), .BUILD_TAG1(32'habcd0123)) dut(
        .aclk(clk), .aresetn(resetn),
        .s_axi_awaddr(awaddr),.s_axi_awprot(3'b0),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(3'b0),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
        .x_arvalid(xa),.x_arready(1'b1),.x_rvalid(xr),.x_rready(1'b1),.x_rlast(xlast),.x_rresp(xresp),
        .w_arvalid(wa),.w_arready(1'b1),.w_rvalid(wr),.w_rready(1'b1),.w_rlast(wlast),.w_rresp(wresp),
        .y_awvalid(ya),.y_awready(1'b1),.y_wvalid(yw),.y_wready(1'b1),
        .y_bvalid(yb),.y_bready(1'b1),.y_bresp(yresp)
    );
    task rd(input [6:0] addr, input [31:0] value, input [1:0] resp);
        begin
            @(negedge clk); araddr=addr; arvalid=1;
            do @(posedge clk); while (!arready);
            @(negedge clk); arvalid=0;
            wait(rvalid);
            repeat(3) begin
                @(negedge clk);
                if (!rvalid || rdata!==value || rresp!==resp)
                    $fatal(1,"READ addr=%h got=%h/%h expected=%h/%h",addr,rdata,rresp,value,resp);
                checks=checks+1;
            end
            rready=1; @(negedge clk); rready=0;
        end
    endtask
    task clear_stats(input [1:0] expected, input integer data_first);
        begin
            @(negedge clk); awaddr=7'h40; wdata=32'h434c5245; wstrb=15;
            if (data_first) begin
                wvalid=1; do @(posedge clk); while(!wready);
                @(negedge clk); wvalid=0;
                repeat(3) @(negedge clk);
                awvalid=1; do @(posedge clk); while(!awready);
                @(negedge clk); awvalid=0;
            end else begin
                awvalid=1; do @(posedge clk); while(!awready);
                @(negedge clk); awvalid=0;
                repeat(3) @(negedge clk);
                wvalid=1; do @(posedge clk); while(!wready);
                @(negedge clk); wvalid=0;
            end
            wait(bvalid);
            repeat(3) begin
                @(negedge clk);
                if(!bvalid || bresp!==expected) $fatal(1,"WRITE response unstable or wrong");
                checks=checks+1;
            end
            bready=1; @(negedge clk); bready=0;
        end
    endtask
    initial begin
        #200000; $fatal(1,"watchdog");
    end
    initial begin
        repeat(5) @(negedge clk); resetn=1;
        rd('h00,'h46383031,0); rd('h04,'h00010000,0);
        rd('h08,'h12345678,0); rd('h0c,'habcd0123,0);
        rd('h10,0,0); rd('h3c,1,0); rd('h44,0,2);
        clear_stats(0,1);
        // Requests remain outstanding until RLAST or B, independently of WLAST.
        @(negedge clk); xa=1; wa=1; ya=1;
        @(negedge clk); xa=0; wa=0; ya=0; yw=1; xr=1; wr=1;
        @(negedge clk); yw=0; xr=0; wr=0;
        rd('h14,1,0); rd('h18,1,0); rd('h1c,1,0); rd('h3c,0,0);
        clear_stats(2,0); rd('h10,16,0); rd('h1c,1,0);
        @(negedge clk); xr=1; xlast=1; xresp=2; wr=1; wlast=1; wresp=3;
        @(negedge clk); xr=0; wr=0;
        rd('h14,0,0); rd('h18,0,0); rd('h1c,1,0);
        rd('h10,19,0); rd('h3c,0,0);
        @(negedge clk); yb=1; yresp=2;
        @(negedge clk); yb=0;
        rd('h1c,0,0); rd('h10,23,0); rd('h3c,1,0);
        rd('h20,1,0); rd('h24,2,0); rd('h28,1,0); rd('h2c,2,0);
        rd('h30,1,0); rd('h34,1,0); rd('h38,1,0);
        clear_stats(0,0); rd('h10,0,0); rd('h20,0,0); rd('h38,0,0);
        // Unexpected completion latches a counter integrity error, not wraparound.
        @(negedge clk); yb=1; yresp=0;
        @(negedge clk); yb=0;
        rd('h1c,0,0); rd('h10,8,0);
        clear_stats(0,1); rd('h10,0,0);
        $display("F8_STATUS_RTL_PASS checks=%0d",checks);
        $finish;
    end
endmodule
