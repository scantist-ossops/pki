// --- BEGIN COPYRIGHT BLOCK ---
// This program is free software; you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation; version 2 of the License.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License along
// with this program; if not, write to the Free Software Foundation, Inc.,
// 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
//
// (C) 2011 Red Hat, Inc.
// All rights reserved.
// --- END COPYRIGHT BLOCK ---

/**
 * 
 */
package com.netscape.cms.servlet.key.model;

import javax.xml.bind.annotation.XmlAccessType;
import javax.xml.bind.annotation.XmlRootElement;
import javax.xml.bind.annotation.XmlAccessorType;
import javax.xml.bind.annotation.XmlElement;

/**
 * @author alee
 *
 */
@XmlRootElement(name="SecurityData")
@XmlAccessorType(XmlAccessType.FIELD)
public class KeyData {
    @XmlElement
    String wrappedPrivateData;
    
    @XmlElement
    String nonceData;

    public KeyData() {
        // required for JAXB (defaults)
    }

    /**
     * @return the wrappedPrivateData
     */
    public String getWrappedPrivateData() {
        return wrappedPrivateData;
    }

    /**
     * @param wrappedPrivateData the wrappedPrivateData to set
     */
    public void setWrappedPrivateData(String wrappedPrivateData) {
        this.wrappedPrivateData = wrappedPrivateData;
    }
    
    /**
     * @return the nonceData
     */

    public String getNonceData() {
        return nonceData;
    }

    /**
     * @param nonceData the nonceData to set
     */

    public void setNonceData(String nonceData) {
        this.nonceData = nonceData;
    }

}
