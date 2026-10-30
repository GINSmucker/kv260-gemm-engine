"""Physical protocol contract checks, including malformed descriptors and payloads."""
import sys,unittest,json
sys.dont_write_bytecode=True
import numpy as np
import axi_codec as c

class ProtocolContracts(unittest.TestCase):
    def test_upstream_synthetic_vectors(self):
        c.synthetic_checks()
    def test_explicit_signed_minimum_and_kept_zero(self):
        x=np.array([[-128,127,0,-1]],np.int8)
        values=np.array([[[0,-128]]],np.int8);pos=np.array([[[0,3]]],np.uint8)
        data,meta=c.pack_sparse(values,pos)
        self.assertEqual(meta[0],12)
        _,_,w=c.unpack_sparse(data,meta,4,1)
        np.testing.assert_array_equal(c.emulate_axi(c.pack_int8(x),data,meta,1,4,1,1),np.array([[128]],np.int32))
        np.testing.assert_array_equal(w.ravel(),[0,0,0,-128])
    def test_malformed_metadata_and_tail(self):
        v=np.array([[[1,2]]],np.int8);p=np.array([[[0,1]]],np.uint8);data,meta=c.pack_sparse(v,p)
        for bad in [bytes(16),bytes([0x44])+bytes(15),bytes([4])+bytes(14)+b'\x01',meta[:-1]]:
            with self.assertRaises(ValueError):c.unpack_sparse(data,bad,4,1)
        with self.assertRaises(ValueError):c.unpack_sparse(data[:-1],meta,4,1)
        with self.assertRaises(ValueError):c.pack_sparse(v,np.array([[[1,1]]],np.uint8))
    def test_dtype_shape_overflow(self):
        for args in [(1,3,2,1),(1,131072,1,0),(True,4,1,0),(1,4,1,2),(0,4,1,0)]:
            with self.assertRaises(ValueError):c.buffer_layout(*args)
        with self.assertRaises(ValueError):c.pack_int8(np.ones((1,4),np.float32))
    def test_descriptor_alignment_capacity_and_overlap(self):
        layout=c.buffer_layout(1,4,1,1);sizes={k:v['allocated_bytes'] for k,v in layout.items()}
        addresses=dict(zip(layout,[64,128,192,256]));c.validate_call(1,4,1,1,addresses,sizes,{(1,4,1)})
        for bad in [dict(addresses,X=65),dict(addresses,Y=64)]:
            with self.assertRaises(ValueError):c.validate_call(1,4,1,1,bad,sizes)
        with self.assertRaises(ValueError):c.validate_call(1,4,1,1,addresses,dict(sizes,X=1))
        with self.assertRaises(ValueError):c.validate_call(1,4,1,1,addresses,sizes,{(197,192,576)})
    def test_dense_ignores_metadata(self):
        x=np.array([[1,-2]],np.int8);w=np.array([[3],[4]],np.int8)
        np.testing.assert_array_equal(c.emulate_axi(c.pack_int8(x),c.pack_int8(w),object(),1,2,1,0),[[-5]])

if __name__=='__main__':unittest.main(verbosity=2)
