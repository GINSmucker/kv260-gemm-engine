// OPT30 packed32 transaction model. Testbench only; single AXI ID per master.
integer mg_log, mg_desc, mg_tx=0, mg_completed=0, mg_good=0, mg_bad=0;
integer mg_active=0, mg_prev_start=0, mg_prev_done=0, mg_valid=0;
integer mg_xar=0,mg_war=0,mg_yaw=0,mg_xr=0,mg_wr=0,mg_yw=0,mg_yb=0;
integer mg_xhead=0,mg_whead=0,mg_xbeat=0,mg_wbeat=0,mg_ybeat=0,mg_ybursts=0,mg_match=0;
integer mg_xlens[0:262143],mg_wlens[0:262143],mg_ylens[0:262143],mg_wlastlens[0:262143];
integer mg_xmulti=0,mg_wmulti=0;
integer mg_xstall=0,mg_wstall=0,mg_awstall=0,mg_ystall=0;
reg [63:0] mg_cycle=0,mg_start_cycle=0,mg_xbase,mg_wbase,mg_ybase,mg_xsize,mg_wsize,mg_ysize;
reg [31:0] mg_M,mg_K,mg_N;
reg [63:0] mg_expected_x,mg_expected_w,mg_expected_y;
integer mg_fault=0;
integer mg_banklog,mg_b,mg_bread[0:7],mg_bwrite[0:7],mg_bmax[0:7];
__MODEL_GEMM_BANK_WIRES__
initial begin
    mg_banklog=$fopen("wbank_monitor.csv","w");
    if(mg_banklog==0) begin $display("AXI_MONITOR_FAIL cannot open bank log"); $finish; end
    $fdisplay(mg_banklog,"transaction,bank,reads,writes,max_address,status");
    mg_log=$fopen("axi_monitor.csv","w");
    mg_desc=$fopen("axi_descriptors.csv","w");
    $fdisplay(mg_desc,"transaction,port,address,beats");
    if(mg_log==0) begin $display("AXI_MONITOR_FAIL cannot open log"); $finish; end
    $fdisplay(mg_log,"transaction,M,K,N,valid,cycles,x_base,w_base,y_base,x_AR,w_AR,y_AW,x_R,w_R,y_W,y_B,x_multibeat,w_multibeat,x_stall,w_stall,aw_stall,y_stall,status");
    if($value$plusargs("MG_FAULT=%d",mg_fault)) $display("MONITOR_NEGATIVE_SELFTEST %d",mg_fault);
end
task mg_fail;
    input [1023:0] message;
    begin
        $display("AXI_MONITOR_FAIL tx=%0d %0s",mg_tx,message);
        $fclose(mg_log); $finish;
    end
endtask
task mg_bank_check;
    input integer bank;
    input ce,we;
    input [14:0] addr;
    begin
        if(ce===1'b1) begin
            if(!mg_active || !mg_valid) mg_fail("W bank outside valid transaction");
            if((^{we,addr})===1'bx) mg_fail("W bank unknown address/control");
            if(addr>=24000 || addr>=mg_wsize/8) mg_fail("W bank bounds");
            if(we) mg_bwrite[bank]=mg_bwrite[bank]+1;
            else mg_bread[bank]=mg_bread[bank]+1;
            if(mg_bmax[bank]<0 || addr>mg_bmax[bank]) mg_bmax[bank]=addr;
        end else if(mg_active && mg_valid && ce!==1'b0) mg_fail("W bank unknown enable");
    end
endtask
task mg_bounds;
    input [63:0] addr,base,count;
    input [7:0] length;
    input [2:0] size;
    input [1:0] burst;
    reg [63:0] high;
    begin
        if((^{addr,base,count,length,size,burst})===1'bx) mg_fail("unknown descriptor");
        high=addr+(length+64'd1)*4;
        if(size!=2 || burst!=1 || length>15 || addr[1:0]!=0) mg_fail("packed32 descriptor");
        if(addr<base || high>base+count || high<addr) mg_fail("buffer bounds");
        if((addr>>12)!=((high-1)>>12)) mg_fail("4KiB crossing burst");
    end
endtask
always @(posedge AESL_clock) begin
    mg_cycle=mg_cycle+1;
    if(`AUTOTB_DUT_INST.ap_rst_n===1'b1) begin
        if(`AUTOTB_DUT_INST.ap_start===1'b1 && mg_prev_start==0) begin
            if(mg_active) mg_fail("second start");
            mg_active=1;mg_tx=mg_tx+1;mg_start_cycle=mg_cycle;
            mg_M=`AUTOTB_DUT_INST.M;mg_K=`AUTOTB_DUT_INST.K;mg_N=`AUTOTB_DUT_INST.N;
            if((^{mg_M,mg_K,mg_N})===1'bx) mg_fail("unknown shape");
            mg_valid=__MODEL_GEMM_VALID_SHAPE__;
            mg_xbase=`AUTOTB_DUT_INST.X;mg_wbase=`AUTOTB_DUT_INST.W;mg_ybase=`AUTOTB_DUT_INST.Y;
            mg_xsize={32'b0,mg_M}*mg_K;mg_wsize={32'b0,mg_K}*mg_N;mg_ysize={32'b0,mg_M}*mg_N*4;
            mg_expected_x=mg_xsize*(({32'b0,mg_N}+64'd63)/64'd64)/64'd4;
            mg_expected_w=mg_wsize/64'd4;mg_expected_y=mg_ysize/64'd4;
            mg_xar=0;mg_war=0;mg_yaw=0;mg_xr=0;mg_wr=0;mg_yw=0;mg_yb=0;
            mg_xhead=0;mg_whead=0;mg_xbeat=0;mg_wbeat=0;mg_ybeat=0;mg_ybursts=0;mg_match=0;
            mg_xmulti=0;mg_wmulti=0;
            mg_xstall=0;mg_wstall=0;mg_awstall=0;mg_ystall=0;
            for(mg_b=0;mg_b<8;mg_b=mg_b+1) begin mg_bread[mg_b]=0;mg_bwrite[mg_b]=0;mg_bmax[mg_b]=-1;end
        end
        for(mg_b=0;mg_b<8;mg_b=mg_b+1) mg_bank_check(mg_b,mg_bank_ce[mg_b],mg_bank_we[mg_b],mg_bank_addr[mg_b]);
        if(gmem0_ARVALID || gmem1_ARVALID || gmem2_AWVALID || gmem2_WVALID ||
           gmem0_AWVALID || gmem1_AWVALID || gmem0_WVALID || gmem1_WVALID || gmem2_ARVALID)
            if(!mg_active || !mg_valid) mg_fail("request outside valid transaction");
        if(gmem0_AWVALID || gmem1_AWVALID || gmem0_WVALID || gmem1_WVALID) mg_fail("const input write");
        if(gmem2_ARVALID) mg_fail("Y read");
        if(gmem0_ARVALID && !gmem0_ARREADY) mg_xstall=mg_xstall+1;
        if(gmem1_ARVALID && !gmem1_ARREADY) mg_wstall=mg_wstall+1;
        if(gmem2_AWVALID && !gmem2_AWREADY) mg_awstall=mg_awstall+1;
        if(gmem2_WVALID && !gmem2_WREADY) mg_ystall=mg_ystall+1;
        if(gmem0_ARVALID && gmem0_ARREADY) begin
            if(mg_xar>=262144) mg_fail("X queue capacity");
            if(mg_fault==1) mg_bounds(mg_xbase+mg_xsize,mg_xbase,mg_xsize,0,2,1);
            mg_bounds(gmem0_ARADDR,mg_xbase,mg_xsize,gmem0_ARLEN,gmem0_ARSIZE,gmem0_ARBURST);
            if(gmem0_ARID!=0) mg_fail("X nonzero ID unsupported");
            mg_xlens[mg_xar]=gmem0_ARLEN+1;mg_xar=mg_xar+1;
            $fdisplay(mg_desc,"%0d,X,%0d,%0d",mg_tx,gmem0_ARADDR,gmem0_ARLEN+1);
            if(gmem0_ARLEN>0) mg_xmulti=mg_xmulti+1;
        end
        if(gmem1_ARVALID && gmem1_ARREADY) begin
            if(mg_war>=262144) mg_fail("W queue capacity");
            mg_bounds(gmem1_ARADDR,mg_wbase,mg_wsize,gmem1_ARLEN,gmem1_ARSIZE,gmem1_ARBURST);
            if(gmem1_ARID!=0) mg_fail("W nonzero ID unsupported");
            mg_wlens[mg_war]=gmem1_ARLEN+1;mg_war=mg_war+1;
            $fdisplay(mg_desc,"%0d,W,%0d,%0d",mg_tx,gmem1_ARADDR,gmem1_ARLEN+1);
            if(gmem1_ARLEN>0) mg_wmulti=mg_wmulti+1;
        end
        if(gmem2_AWVALID && gmem2_AWREADY) begin
            if(mg_yaw>=262144) mg_fail("Y queue capacity");
            mg_bounds(gmem2_AWADDR,mg_ybase,mg_ysize,gmem2_AWLEN,gmem2_AWSIZE,gmem2_AWBURST);
            if(gmem2_AWID!=0) mg_fail("Y nonzero ID unsupported");
            mg_ylens[mg_yaw]=gmem2_AWLEN+1;mg_yaw=mg_yaw+1;
            $fdisplay(mg_desc,"%0d,Y,%0d,%0d",mg_tx,gmem2_AWADDR,gmem2_AWLEN+1);
        end
        if(gmem0_RVALID && gmem0_RREADY) begin
            if(mg_xhead>=mg_xar || gmem0_RRESP!==0 || gmem0_RID!=0) mg_fail("X response");
            mg_xbeat=mg_xbeat+1;mg_xr=mg_xr+1;
            if((gmem0_RLAST ^ (mg_fault==2)) !== (mg_xbeat==mg_xlens[mg_xhead])) mg_fail("X RLAST");
            if(gmem0_RLAST) begin mg_xhead=mg_xhead+1;mg_xbeat=0;end
        end
        if(gmem1_RVALID && gmem1_RREADY) begin
            if(mg_whead>=mg_war || gmem1_RRESP!==0 || gmem1_RID!=0) mg_fail("W response");
            mg_wbeat=mg_wbeat+1;mg_wr=mg_wr+1;
            if(gmem1_RLAST !== (mg_wbeat==mg_wlens[mg_whead])) mg_fail("W RLAST");
            if(gmem1_RLAST) begin mg_whead=mg_whead+1;mg_wbeat=0;end
        end
        // AW and W may arrive in either order. Match completed W bursts to AW FIFO.
        if(gmem2_WVALID && gmem2_WREADY) begin
            if(gmem2_WSTRB!==4'b1111 || (gmem2_WLAST!==0 && gmem2_WLAST!==1)) mg_fail("Y strobe/LAST unknown");
            mg_ybeat=mg_ybeat+1;mg_yw=mg_yw+1;
            if(gmem2_WLAST) begin
                if(mg_ybursts>=262144) mg_fail("W burst queue capacity");
                mg_wlastlens[mg_ybursts]=mg_ybeat;mg_ybursts=mg_ybursts+1;mg_ybeat=0;
            end
        end
        if(mg_match<mg_yaw && mg_match<mg_ybursts) begin
            if(mg_ylens[mg_match]!=mg_wlastlens[mg_match]) mg_fail("Y WLAST length");
            mg_match=mg_match+1;
        end
        if(gmem2_BVALID && gmem2_BREADY) begin
            if(gmem2_BRESP!==0 || gmem2_BID!=0) mg_fail("Y response");
            mg_yb=mg_yb+1;
            if(mg_yb>mg_match) mg_fail("B without completed AW/W");
        end
        if(`AUTOTB_DUT_INST.ap_done===1'b1 && mg_prev_done==0) begin
            if(!mg_active) mg_fail("done without start");
            if(mg_xhead!=mg_xar || mg_whead!=mg_war || mg_yb!=mg_yaw || mg_match!=mg_yaw || mg_ybursts!=mg_yaw || mg_xbeat!=0 || mg_wbeat!=0 || mg_ybeat!=0) mg_fail("Done before drain");
            if(mg_valid) begin
                if(`AUTOTB_DUT_INST.ap_return!==0) mg_fail("valid return");
                $display("MG_DIRECT_COUNTS tx=%0d X=%0d/%0d W=%0d/%0d Y=%0d/%0d",mg_tx,mg_xr,mg_expected_x,mg_wr,mg_expected_w,mg_yw,mg_expected_y);
                if(mg_fault==3) mg_fail("packed traffic count injected fault");
                if(mg_xr!=mg_expected_x || mg_wr!=mg_expected_w || mg_yw!=mg_expected_y) mg_fail("packed traffic count");
                if(mg_xmulti==0 || mg_wmulti==0) mg_fail("no real X/W burst");
                mg_good=mg_good+1;
            end else begin
                if(`AUTOTB_DUT_INST.ap_return!==1 || mg_xar!=0 || mg_war!=0 || mg_yaw!=0 || mg_xr!=0 || mg_wr!=0 || mg_yw!=0 || mg_yb!=0) mg_fail("invalid traffic/return");
                mg_bad=mg_bad+1;
            end
            mg_completed=mg_completed+1;
            for(mg_b=0;mg_b<8;mg_b=mg_b+1) begin
                $display("MG_BANK tx=%0d bank=%0d reads=%0d writes=%0d max=%0d expected_words=%0d",mg_tx,mg_b,mg_bread[mg_b],mg_bwrite[mg_b],mg_bmax[mg_b],mg_wsize/8);
                if(mg_valid && (mg_bwrite[mg_b]!=mg_wsize/8 || mg_bread[mg_b]==0 || mg_bmax[mg_b]!=mg_wsize/8-1)) mg_fail("W bank coverage/count");
                if(!mg_valid && (mg_bwrite[mg_b]!=0 || mg_bread[mg_b]!=0)) mg_fail("invalid W bank traffic");
                $fdisplay(mg_banklog,"%0d,%0d,%0d,%0d,%0d,PASS",mg_tx,mg_b,mg_bread[mg_b],mg_bwrite[mg_b],mg_bmax[mg_b]);
            end
            $fflush(mg_banklog);
            $fdisplay(mg_log,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,PASS",mg_tx,mg_M,mg_K,mg_N,mg_valid,mg_cycle-mg_start_cycle,mg_xbase,mg_wbase,mg_ybase,mg_xar,mg_war,mg_yaw,mg_xr,mg_wr,mg_yw,mg_yb,mg_xmulti,mg_wmulti,mg_xstall,mg_wstall,mg_awstall,mg_ystall);
            $fflush(mg_log);mg_active=0;
            if(mg_completed==16) begin
                if(mg_good!=8 || mg_bad!=8) mg_fail("profile coverage");
                $display("AXI_MONITOR_PASS valid=8 invalid=8 completed=16");
            end
        end
        mg_prev_start=`AUTOTB_DUT_INST.ap_start;mg_prev_done=`AUTOTB_DUT_INST.ap_done;
    end else begin
        if(mg_tx>0) mg_fail("reset between or within requests");
        mg_prev_start=0;mg_prev_done=0;
    end
end
