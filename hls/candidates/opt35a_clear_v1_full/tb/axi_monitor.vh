// Inserted only inside the generated co-simulation testbench, never the DUT.
// Checks all three AXI memory routes with the actual latched shape and base.
integer mg_log, mg_tx=0, mg_done_count=0, mg_valid_count=0, mg_invalid_count=0;
integer mg_active=0, mg_prev_start=0, mg_prev_done=0, mg_valid=0;
integer mg_px=0, mg_pw=0, mg_py=0;
integer mg_xreq=0, mg_wreq=0, mg_yreq=0;
reg [63:0] mg_cycle=0, mg_start_cycle=0;
reg [63:0] mg_xbase, mg_wbase, mg_ybase, mg_xsize, mg_wsize, mg_ysize;
reg [63:0] mg_xbytes=0, mg_wbytes=0, mg_ybytes=0;
reg [31:0] mg_M, mg_K, mg_N;

initial begin
    mg_log=$fopen("axi_monitor.csv","w");
    if(mg_log==0) begin $display("AXI_MONITOR_FAIL cannot open log"); $finish; end
    $fdisplay(mg_log,"transaction,M,K,N,valid,cycles,x_requests,w_requests,y_requests,x_bytes,w_bytes,y_bytes,status");
end

task mg_fail;
    input [1023:0] message;
    begin
        $display("AXI_MONITOR_FAIL tx=%0d %0s",mg_tx,message);
        $fdisplay(mg_log,"FAIL,%0d,%0s",mg_tx,message);
        $fclose(mg_log);
        $finish;
    end
endtask

task mg_read_bounds;
    input [63:0] addr, base, count;
    input [7:0] length;
    input [2:0] size;
    input [1:0] burst;
    reg [63:0] beat, low, high;
    begin
        beat=64'd1<<size;
        if((^addr)===1'bx || (^base)===1'bx || (^count)===1'bx || (^length)===1'bx || (^size)===1'bx) mg_fail("unknown AXI address or descriptor");
        low=(addr/beat)*beat;
        high=low+(length+64'd1)*beat;
        if(burst!=2'b01 || size>2) mg_fail("unsupported AXI burst/size");
        if(addr<base || low<base || high>base+count || high<low) mg_fail("AXI burst exceeds exact logical buffer");
    end
endtask

always @(posedge AESL_clock) begin
    mg_cycle=mg_cycle+1;
    if(`AUTOTB_DUT_INST.ap_rst_n===1'b1) begin
        if(`AUTOTB_DUT_INST.ap_start===1'b1 && mg_prev_start==0) begin
            if(mg_active) mg_fail("second start before completed transaction");
            mg_active=1; mg_tx=mg_tx+1; mg_start_cycle=mg_cycle;
            mg_M=`AUTOTB_DUT_INST.M; mg_K=`AUTOTB_DUT_INST.K; mg_N=`AUTOTB_DUT_INST.N;
            if((^{mg_M,mg_K,mg_N})===1'bx) mg_fail("unknown shape at start");
            mg_valid=__MODEL_GEMM_VALID_SHAPE__;
            mg_xbase=`AUTOTB_DUT_INST.X; mg_wbase=`AUTOTB_DUT_INST.W; mg_ybase=`AUTOTB_DUT_INST.Y;
            mg_xsize={32'b0,mg_M}*{32'b0,mg_K};
            mg_wsize={32'b0,mg_K}*{32'b0,mg_N};
            mg_ysize={32'b0,mg_M}*{32'b0,mg_N}*64'd4;
            mg_px=0; mg_pw=0; mg_py=0;
            mg_xreq=0; mg_wreq=0; mg_yreq=0;
            mg_xbytes=0; mg_wbytes=0; mg_ybytes=0;
        end

        // Reject a request even if READY is low: invalid shapes issue no AXI traffic.
        if(gmem0_ARVALID || gmem1_ARVALID || gmem2_AWVALID || gmem2_WVALID ||
           gmem0_AWVALID || gmem1_AWVALID || gmem0_WVALID || gmem1_WVALID || gmem2_ARVALID) begin
            if(!mg_active || !mg_valid) mg_fail("data request outside valid transaction");
        end
        if(gmem0_AWVALID || gmem1_AWVALID || gmem0_WVALID || gmem1_WVALID) mg_fail("attempted write to const input");
        if(gmem2_ARVALID) mg_fail("attempted output read / DDR partial-sum dependency");

        if(gmem0_ARVALID && gmem0_ARREADY) begin
            mg_read_bounds(gmem0_ARADDR,mg_xbase,mg_xsize,gmem0_ARLEN,gmem0_ARSIZE,gmem0_ARBURST);
            mg_px=mg_px+1; mg_xreq=mg_xreq+1;
            mg_xbytes=mg_xbytes+(gmem0_ARLEN+64'd1)*(64'd1<<gmem0_ARSIZE);
        end
        if(gmem1_ARVALID && gmem1_ARREADY) begin
            mg_read_bounds(gmem1_ARADDR,mg_wbase,mg_wsize,gmem1_ARLEN,gmem1_ARSIZE,gmem1_ARBURST);
            mg_pw=mg_pw+1; mg_wreq=mg_wreq+1;
            mg_wbytes=mg_wbytes+(gmem1_ARLEN+64'd1)*(64'd1<<gmem1_ARSIZE);
        end
        if(gmem2_AWVALID && gmem2_AWREADY) begin
            mg_read_bounds(gmem2_AWADDR,mg_ybase,mg_ysize,gmem2_AWLEN,gmem2_AWSIZE,gmem2_AWBURST);
            if(gmem2_AWSIZE!=2) mg_fail("unexpected output beat size");
            mg_py=mg_py+1; mg_yreq=mg_yreq+1;
        end
        if(gmem0_RVALID && gmem0_RREADY) begin
            if(gmem0_RRESP!=0) mg_fail("X read response error");
            if(gmem0_RLAST) mg_px=mg_px-1;
        end
        if(gmem1_RVALID && gmem1_RREADY) begin
            if(gmem1_RRESP!=0) mg_fail("W read response error");
            if(gmem1_RLAST) mg_pw=mg_pw-1;
        end
        if(gmem2_WVALID && gmem2_WREADY) begin
            if(gmem2_WSTRB!=4'b1111) mg_fail("output write has incomplete byte enables");
            mg_ybytes=mg_ybytes+4;
        end
        if(gmem2_BVALID && gmem2_BREADY) begin
            if(gmem2_BRESP!=0) mg_fail("Y write response error");
            mg_py=mg_py-1;
        end
        if(mg_px<0 || mg_pw<0 || mg_py<0) mg_fail("response without matching request");

        if(`AUTOTB_DUT_INST.ap_done===1'b1 && mg_prev_done==0) begin
            if(!mg_active) mg_fail("done without start");
            if(mg_px!=0 || mg_pw!=0 || mg_py!=0) mg_fail("done before AXI responses drain");
            if(mg_valid) begin
                if(`AUTOTB_DUT_INST.ap_return!=0 || mg_xreq==0 || mg_wreq==0 || mg_yreq==0 || mg_ybytes!=mg_ysize) mg_fail("valid completion/error/output byte count");
                mg_valid_count=mg_valid_count+1;
            end else begin
                if(`AUTOTB_DUT_INST.ap_return!=1 || mg_xreq!=0 || mg_wreq!=0 || mg_yreq!=0) mg_fail("invalid shape did not reject before memory");
                mg_invalid_count=mg_invalid_count+1;
            end
            mg_done_count=mg_done_count+1;
            $fdisplay(mg_log,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,PASS",mg_tx,mg_M,mg_K,mg_N,mg_valid,mg_cycle-mg_start_cycle,mg_xreq,mg_wreq,mg_yreq,mg_xbytes,mg_wbytes,mg_ybytes);
            $fflush(mg_log);
            mg_active=0;
            if(mg_done_count==16) begin
                if(mg_valid_count!=8 || mg_invalid_count!=8) mg_fail("transaction coverage");
                $display("AXI_MONITOR_PASS valid=8 invalid=8 completed=16");
            end
        end
        mg_prev_start=`AUTOTB_DUT_INST.ap_start;
        mg_prev_done=`AUTOTB_DUT_INST.ap_done;
    end else begin
        if(mg_active) mg_fail("reset during active transaction");
        mg_prev_start=0; mg_prev_done=0;
    end
end
